import contextlib
import gzip
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import agents, api, auth
from app.auth import Actor
from app.config import Settings
from app.core import (
    AgentAudit,
    AgentProposal,
    Base,
    DispatchOutbox,
    Job,
    ProjectMembership,
    User,
)


class FakeAgentAdapter:
    def __init__(self, output=None):
        self.output = output or {
            "tool_calls": [
                {"name": "create_render_job", "arguments": {"operation": "render_test"}}
            ]
        }
        self.calls = []

    def propose(self, task, tools):
        self.calls.append((task, tools))
        return self.output


@contextlib.contextmanager
def _adapter_lease_context():
    from app import inference_coordination as ic
    lease = ic.InferenceLease(owner="test-agent-adapter")
    token = ic._set_active_lease(lease)
    try:
        yield lease
    finally:
        ic._reset_active_lease(token)


@pytest.fixture(autouse=True)
def _allow_direct_adapter_inference(monkeypatch):
    from app import inference_coordination as ic
    monkeypatch.setattr(ic, "ensure_lease_owned", lambda *_a, **_k: None)
    token = ic._set_active_lease(ic.InferenceLease(owner="test-agent-adapter"))
    try:
        yield
    finally:
        ic._reset_active_lease(token)


@pytest.fixture
def agent_api(tmp_path, monkeypatch):
    database = create_engine(
        f"sqlite:///{tmp_path / 'agents.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    test_sessions = sessionmaker(database, expire_on_commit=False)
    state = {
        "actor": Actor(name="owner", roles=["viewer"], user_id="owner-id"),
        "dispatch_failure": False,
    }
    adapter = FakeAgentAdapter()
    enqueued = []
    with test_sessions() as db:
        for username in ("owner", "editor", "reviewer", "outsider"):
            db.add(
                User(
                    id=f"{username}-id",
                    username=username,
                    password_hash="unused",
                )
            )
        db.commit()

    def session_override():
        with test_sessions() as db:
            yield db

    def _noop_lease(*_args, **_kwargs):
        from contextlib import nullcontext
        return nullcontext(object())

    def dispatch_job(job_id):
        enqueued.append(job_id)
        with test_sessions() as db:
            job = db.get(Job, job_id)
            outbox = db.get(DispatchOutbox, job_id)
            if state["dispatch_failure"]:
                job.status = "dispatch_pending"
                outbox.status = "pending"
                db.commit()
                return False
            job.status = "queued"
            outbox.status = "dispatched"
            db.commit()
        return True

    monkeypatch.setattr(api, "get_settings", lambda: Settings(agents_enabled=True))
    monkeypatch.setattr(api, "get_agent_adapter", lambda: adapter)
    monkeypatch.setattr(api, "dispatch_outbox", dispatch_job)
    monkeypatch.setattr(api, "heavy_inference_lease", _noop_lease)
    api.app.dependency_overrides[api.db_session] = session_override
    api.app.dependency_overrides[auth.get_current_actor] = lambda: state["actor"]
    with TestClient(api.app) as client:
        yield client, state, adapter, enqueued, test_sessions
    api.app.dependency_overrides.clear()
    database.dispose()


def create_project(client, name="Synthetic project"):
    response = client.post("/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def test_agent_tool_output_allows_only_the_fixed_render_action():
    result = agents.validate_agent_output(
        {
            "tool_calls": [
                {"name": "create_render_job", "arguments": {"operation": "render_test"}}
            ]
        }
    )
    assert result.tool_calls[0].name == "create_render_job"
    assert agents.ALLOWED_TOOLS == ("create_render_job",)

    for output in (
        {"tool_calls": [{"name": "shell", "arguments": {"command": "id"}}]},
        {"tool_calls": [{"name": "create_render_job", "arguments": {"operation": "render_test", "prompt": "private"}}]},
        {"tool_calls": [{"name": "create_render_job", "arguments": {"operation": "wan22_t2v"}}]},
        {"tool_calls": []},
        {"tool_calls": [{"name": "create_render_job", "arguments": {"operation": "render_test"}}] * 2},
        {"tool_calls": [{"name": "create_render_job", "arguments": {"operation": "render_test"}, "shell": True}]},
    ):
        with pytest.raises(ValueError, match="Malformed agent output"):
            agents.validate_agent_output(output)


def test_default_adapter_is_disabled():
    with pytest.raises(RuntimeError, match="disabled"):
        agents.DisabledAgentAdapter().propose("synthetic task", agents.ALLOWED_TOOLS)


def test_enabled_agent_requires_explicit_pinned_local_configuration(monkeypatch):
    monkeypatch.setattr(agents, "get_settings", lambda: Settings(agents_enabled=True))
    with pytest.raises(agents.AgentUnavailable, match="unconfigured"):
        agents.get_agent_adapter()
    with pytest.raises(agents.AgentUnavailable, match="configuration_invalid"):
        agents.LocalOllamaAdapter("https://public.example", "model", "a" * 64)
    with pytest.raises(agents.AgentUnavailable, match="configuration_invalid"):
        agents.LocalOllamaAdapter("http://localhost:11434", "model", "latest", allow_host_endpoint=True)
    with pytest.raises(agents.AgentUnavailable, match="configuration_invalid"):
        agents.LocalOllamaAdapter("http://localhost:not-a-port", "model", "a" * 64)


def test_local_adapter_checks_identity_and_requests_structured_output(monkeypatch):
    requests = []
    timeouts = []
    digest = "a" * 64

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local-model", "digest": digest}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={
                "capabilities": ["completion"],
                "thinking": {"values": [False, True], "default": True},
            })
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert [message["role"] for message in payload["messages"]] == ["system", "user"]
        assert payload["messages"][1]["content"] == "synthetic"
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["format"]["type"] == "object"
        assert payload["options"]["num_predict"] == 3072
        assert "num_gpu" not in payload["options"]
        return httpx.Response(200, json={"done": True, "done_reason": "stop",
                                         "message": {"content": json.dumps({
            "tool_calls": [{"name": "create_render_job", "arguments": {"operation": "render_test"}}]
        })}})

    client = httpx.Client
    def create_client(**kwargs):
        timeouts.append(kwargs["timeout"])
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(agents.httpx, "Client", create_client)
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "local-model", digest, allow_host_endpoint=True)
    assert agents.validate_agent_output(adapter.propose("synthetic", agents.ALLOWED_TOOLS))
    assert [request.url.path for request in requests] == [
        "/api/tags", "/api/show", "/api/chat", "/api/tags", "/api/show",
    ]
    assert [timeout.read for timeout in timeouts] == [15, 15, 90, 15, 15]
    assert all(
        (timeout.connect, timeout.write, timeout.pool) == (5, 15, 5)
        for timeout in timeouts
    )


def test_local_adapter_training_generation_runs_validated_bounded_stages(monkeypatch):
    digest = "a" * 64
    outputs = {
        "outline": {
            "title": "Reading a visible label",
            "audience": "Training reviewers",
            "prerequisites": [],
            "learning_objectives": [{
                "text": "Identify the visible library settings label.",
                "step_ids": ["step-1"],
            }],
            "chapters": [{
                "id": "chapter-1", "title": "Read the visible label.",
                "step_id": "step-1",
            }],
        },
        "procedure": {
            "steps": [{"step_id": "step-1", "evidence_ids": ["frame-0"]}],
        },
        "narration": {
            "segments": [{
                "step_id": "step-1",
                "text": "The screen displays the library settings label.",
            }],
        },
        "activities": {
            "exercises": ["step-1"],
            "validation_checkpoints": ["step-1"],
            "troubleshooting": ["step-1"],
            "assessment": ["step-1"],
        },
    }
    prompt_stages = {
        "Create a concise training outline": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration": "narration",
        "Select approved step IDs": "activities",
    }
    seen = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "model", "digest": digest}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={
                "capabilities": ["completion"],
                "thinking": {"values": [False, True], "default": True},
            })
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{
                "name": "model", "size": 1_000_000, "size_vram": 0,
            }]})
        payload = json.loads(request.content)
        assert payload["options"]["num_ctx"] == 4096
        assert payload["options"]["num_predict"] in {768, 256, 512, 384}
        assert payload["think"] is False
        system = payload["messages"][0]["content"]
        stage_name = next(name for marker, name in prompt_stages.items() if marker in system)
        seen.append((stage_name, payload["options"]["num_predict"]))
        assert payload["format"]["type"] == "object"
        return httpx.Response(200, json={
            "done": True, "done_reason": "stop",
            "prompt_eval_count": 120, "eval_count": 24, "total_duration": 1_000_000_000,
            "message": {"content": json.dumps(outputs[stage_name]), "thinking": ""},
        })

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", digest, allow_host_endpoint=True)

    evidence = {
        "duration_ms": 1000,
        "approved_steps": [{
            "id": "step-1", "start_ms": 0, "end_ms": 1000,
            "description": "Read the visible library settings label.",
            "evidence": ["frame-0"], "decision": "accepted",
            "confidence": "high", "evidence_category": "direct_observation",
        }],
        "frames": [{"id": "frame-0", "timestamp_ms": 0, "ocr": "Settings"}],
        "request": {"learner_level": "beginner", "objectives": "", "feedback": ""},
    }
    result = adapter.generate_training(evidence, lambda: None)

    assert result.draft.title == "Reading a visible label"
    assert result.draft.chapters[0].start_ms == 0
    assert result.draft.chapters[0].end_ms == 1000
    assert result.draft.script[0].evidence == ["frame-0"]
    assert result.draft.troubleshooting[0].kind == "supplemental"
    assert [name for name, _ in seen] == [
        "outline", "procedure", "narration", "activities",
    ]
    assert len(result.stages) == 4
    assert all(stage["schema_validation_result"] == "passed" for stage in result.stages)
    assert all(stage["evidence_validation_result"] == "passed" for stage in result.stages)
    assert all(stage["schema_version"] == 1 for stage in result.stages)
    assert all(stage["prompt_tokens"] == 120 for stage in result.stages)
    assert all(stage["generated_tokens"] == 24 for stage in result.stages)
    assert all(stage["thinking_requested"] is False for stage in result.stages)
    assert all(stage["thinking_disable_supported"] is True for stage in result.stages)
    assert all(stage["thinking_default"] is True for stage in result.stages)
    assert all(stage["ollama_version"] == "0.40.16" for stage in result.stages)
    assert all(stage["ollama_model_memory"]["gpu_bytes"] == 0 for stage in result.stages)


@pytest.mark.parametrize(("reason", "content", "error", "validation"), [
    ("length", '{"partial":"PRIVATE-SYNTHETIC-OUTPUT"', "generation_incomplete", "not_run"),
    ("stop", "{malformed", "generation_invalid_json", "invalid_json"),
    ("stop", '{"title":"incomplete schema"}', "generation_schema_invalid", "failed"),
])
def test_training_stage_failures_capture_safe_structured_diagnostics(
    monkeypatch, reason, content, error, validation
):
    digest = "a" * 64

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "model", "digest": digest}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={
                "capabilities": ["completion"],
                "thinking": {"values": [False, True], "default": True},
            })
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        return httpx.Response(200, json={
            "done": True,
            "done_reason": reason,
            "prompt_eval_count": 84,
            "eval_count": 128,
            "total_duration": 2_500_000_000,
            "message": {"content": content, "thinking": "synthetic-private-thinking"},
        })

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", digest, allow_host_endpoint=True)
    evidence = {
        "duration_ms": 1000,
        "approved_steps": [{
            "id": "step-1", "start_ms": 0, "end_ms": 1000,
            "description": "Review the synthetic screen.",
            "evidence": ["frame-0"], "decision": "accepted",
            "confidence": "high", "evidence_category": "direct_observation",
        }],
        "frames": [{"id": "frame-0", "timestamp_ms": 0}],
        "request": {},
    }

    with pytest.raises(agents.AgentUnavailable, match=error) as failure:
        adapter.generate_training(evidence, lambda: None)

    diagnostic = failure.value.diagnostics[0]
    assert diagnostic["stage"] == "outline"
    assert diagnostic["model_identifier"] == "model"
    assert diagnostic["model_digest"] == digest
    assert diagnostic["ollama_version"] == "0.40.16"
    assert diagnostic["api_route"] == "/api/chat"
    assert diagnostic["context_tokens"] == 4096
    assert diagnostic["output_token_limit"] == 768
    assert diagnostic["completion_reason"] == reason
    assert diagnostic["prompt_tokens"] == 84
    assert diagnostic["generated_tokens"] == 128
    assert diagnostic["response_length_chars"] == len(content)
    assert diagnostic["schema_validation_result"] == validation
    assert diagnostic["thinking_disable_supported"] is True
    assert diagnostic["thinking_behavior"] == "disable_control_not_honored"
    assert "ollama_model_memory" in diagnostic
    assert "PRIVATE-SYNTHETIC-OUTPUT" not in json.dumps(diagnostic)
    assert "synthetic-private-thinking" not in json.dumps(diagnostic)


def test_local_adapter_rejects_token_truncated_chat_response(monkeypatch):
    digest = "a" * 64

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "model", "digest": digest}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        return httpx.Response(200, json={
            "done": True, "done_reason": "length",
            "message": {"content": json.dumps({
                "tool_calls": [{
                    "name": "create_render_job",
                    "arguments": {"operation": "render_test"},
                }],
            })},
        })

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", digest, allow_host_endpoint=True)
    with pytest.raises(agents.AgentUnavailable, match="generation_incomplete"):
        adapter.propose("synthetic", agents.ALLOWED_TOOLS)


def test_local_adapter_rejects_mismatched_model_without_generation(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": "model", "digest": "b" * 64}]})

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", "a" * 64, allow_host_endpoint=True)
    with pytest.raises(agents.AgentUnavailable, match="identity"):
        adapter.propose("synthetic", agents.ALLOWED_TOOLS)
    assert paths == ["/api/tags"]


def test_local_adapter_rejects_identity_change_after_generation(monkeypatch):
    digest = "a" * 64
    tag_calls = 0

    def handler(request):
        nonlocal tag_calls
        if request.url.path == "/api/tags":
            tag_calls += 1
            return httpx.Response(200, json={"models": [{
                "name": "model", "digest": digest if tag_calls == 1 else "b" * 64,
            }]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        return httpx.Response(200, json={
            "done": True, "done_reason": "stop", "message": {"content": "{}"},
        })

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", digest, allow_host_endpoint=True)
    with pytest.raises(agents.AgentUnavailable, match="identity"):
        adapter.propose("synthetic", agents.ALLOWED_TOOLS)


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"x" * (1024 * 1024 + 1)),
    httpx.Response(200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"synthetic")),
])
def test_local_adapter_bounds_provider_responses(monkeypatch, response):
    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(lambda _request: response), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", "a" * 64, allow_host_endpoint=True)
    with pytest.raises(agents.AgentUnavailable, match="response"):
        adapter.propose("synthetic", agents.ALLOWED_TOOLS)


def test_local_adapter_checks_deadline_on_small_stream_chunks(monkeypatch):
    class TinyChunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{"
            yield b"}"

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=TinyChunks())), **kwargs
    ))
    clock = iter([0.0, 91.0])
    monkeypatch.setattr(agents, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", "a" * 64, allow_host_endpoint=True)
    with pytest.raises(agents.AgentUnavailable, match="timeout"):
        adapter.propose("synthetic", agents.ALLOWED_TOOLS)


def test_local_adapter_reports_provider_timeout_and_preserves_cancellation(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", "a" * 64, allow_host_endpoint=True)
    with pytest.raises(agents.AgentUnavailable, match="agent_provider_timeout"):
        adapter.propose("synthetic", agents.ALLOWED_TOOLS)

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.generate_training({}, cancel)


def test_local_status_requires_validated_generation_not_only_model_metadata(monkeypatch):
    digest = "a" * 64

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "model", "digest": digest}]})
        return httpx.Response(200, json={"capabilities": ["completion"]})

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", digest, allow_host_endpoint=True)
    assert adapter.status(None)["status"] == "degraded"
    assert adapter.status(adapter.provenance())["status"] == "ready"
    assert adapter.status({**adapter.provenance(), "endpoint_sha256": "b" * 64})["status"] == "degraded"


@pytest.mark.parametrize(("response", "expected"), [
    (httpx.ConnectError("offline"), "unavailable"),
    (httpx.Response(200, json={"models": []}), "degraded"),
])
def test_local_status_reports_unavailable_or_degraded(monkeypatch, response, expected):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    client = httpx.Client
    monkeypatch.setattr(agents.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    adapter = agents.LocalOllamaAdapter("http://localhost:11434", "model", "a" * 64, allow_host_endpoint=True)
    assert adapter.status(None)["status"] == expected


def test_proposals_are_project_scoped_validated_and_approval_gated(agent_api):
    client, state, adapter, enqueued, test_sessions = agent_api
    project_id = create_project(client)
    private_task = "synthetic private prompt sentinel"
    response = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": private_task},
    )
    assert response.status_code == 201
    proposal = response.json()
    assert proposal["tool"] == "create_render_job"
    assert proposal["status"] == "pending"
    assert private_task not in response.text
    assert adapter.calls == [(private_task, agents.ALLOWED_TOOLS)]

    with test_sessions() as db:
        saved = db.get(AgentProposal, proposal["id"])
        events = db.scalars(select(AgentAudit)).all()
        assert saved.arguments == {"operation": "render_test"}
        assert private_task not in str(saved.arguments)
        assert [event.action for event in events] == ["proposed"]

    state["actor"] = Actor(name="owner", roles=["viewer"], user_id="owner-id")
    self_approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/approve"
    )
    assert self_approval.status_code == 403
    assert enqueued == []

    state["actor"] = Actor(name="owner", roles=["viewer"], user_id="owner-id")
    add_reviewer = client.put(
        f"/v1/projects/{project_id}/members/reviewer",
        json={"role": "reviewer"},
    )
    assert add_reviewer.status_code == 200
    assert add_reviewer.json() == {"username": "reviewer", "role": "reviewer"}
    state["actor"] = Actor(name="reviewer", roles=["viewer"], user_id="reviewer-id")
    approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/approve"
    )
    assert approval.status_code == 200
    assert approval.json()["status"] == "approved"
    assert approval.json()["job"]["operation"] == "render_test"
    assert enqueued == [approval.json()["job"]["id"]]

    repeated = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/approve"
    )
    assert repeated.status_code == 409
    with test_sessions() as db:
        assert [event.action for event in db.scalars(select(AgentAudit).order_by(AgentAudit.id))] == [
            "proposed",
            "approved",
        ]


def test_cross_project_and_agent_service_approval_are_denied(agent_api):
    client, state, _adapter, enqueued, test_sessions = agent_api
    project_id = create_project(client)
    response = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "make a render"},
    )
    proposal_id = response.json()["id"]

    outsider = Actor(name="outsider", roles=["viewer"], user_id="outsider-id")
    state["actor"] = outsider
    hidden = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "cross-project request"},
    )
    assert hidden.status_code == 404

    state["actor"] = Actor(name="outsider", roles=["viewer"], user_id="outsider-id")
    outsider_project = create_project(client, "Other synthetic project")
    state["actor"] = Actor(name="owner", roles=["viewer"], user_id="owner-id")
    cross_project = client.post(
        f"/v1/projects/{outsider_project}/agent/proposals",
        json={"task": "cross-project request"},
    )
    assert cross_project.status_code == 404

    with test_sessions() as db:
        db.add(
            ProjectMembership(
                project_id=project_id,
                user_id="reviewer-id",
                role="reviewer",
            )
        )
        db.commit()
    state["actor"] = Actor(
        name="reviewer",
        roles=["viewer", "agent_service"],
        user_id="reviewer-id",
    )
    service_approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal_id}/approve"
    )
    assert service_approval.status_code == 403
    assert enqueued == []


def test_malformed_agent_output_is_not_persisted(agent_api, caplog):
    client, _state, adapter, _, test_sessions = agent_api
    project_id = create_project(client)
    adapter.output = {
        "tool_calls": [{"name": "shell", "arguments": {"command": "id"}}]
    }
    private_task = "synthetic prompt sentinel that must not be logged"

    response = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": private_task},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Malformed agent output"
    assert private_task not in response.text
    assert private_task not in caplog.text
    with test_sessions() as db:
        assert db.scalars(select(AgentProposal)).all() == []
        assert db.scalars(select(AgentAudit)).all() == []


def test_agent_service_cannot_create_projects_or_bypass_approval(agent_api):
    client, state, _, _, test_sessions = agent_api
    project_id = create_project(client)
    state["actor"] = Actor(name="agent", roles=["producer", "agent_service"])

    create_project_response = client.post(
        "/v1/projects",
        json={"name": "Agent-created project"},
    )
    direct_job_response = client.post(
        "/v1/jobs",
        json={"operation": "render_test"},
    )
    assert create_project_response.status_code == 403
    assert direct_job_response.status_code == 403

    with test_sessions() as db:
        assert db.scalars(select(Job)).all() == []
        assert db.scalar(
            select(ProjectMembership).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == "owner-id",
            )
        ) is not None


def test_integration_identity_can_submit_jobs_when_agents_are_disabled(
    agent_api, monkeypatch
):
    client, state, _, enqueued, test_sessions = agent_api
    settings = Settings(
        agents_enabled=False,
        forge_openwebui_token="openwebui-token",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    actor = auth.LocalAuthProvider().authenticate(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="openwebui-token")
    )
    state["actor"] = actor
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    response = client.post("/v1/jobs", json={"operation": "render_test"})

    assert response.status_code == 202
    assert "agent_service" not in actor.roles
    assert enqueued == [response.json()["id"]]
    with test_sessions() as db:
        assert db.get(Job, response.json()["id"]).status == "queued"


def test_approved_dispatch_pending_job_can_be_redispatched(agent_api, monkeypatch):
    client, state, _, enqueued, test_sessions = agent_api
    project_id = create_project(client)
    add_editor = client.put(
        f"/v1/projects/{project_id}/members/editor",
        json={"role": "editor"},
    )
    assert add_editor.status_code == 200
    state["actor"] = Actor(name="editor", roles=["viewer"], user_id="editor-id")
    proposal = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "make a render"},
    ).json()
    state["actor"] = Actor(name="owner", roles=["viewer"], user_id="owner-id")

    state["dispatch_failure"] = True
    approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/approve"
    )
    assert approval.status_code == 200
    job_id = approval.json()["job"]["id"]
    assert approval.json()["job"]["status"] == "dispatch_pending"

    state["dispatch_failure"] = False
    state["actor"] = Actor(name="owner", roles=["viewer"], user_id="owner-id")
    redispatch = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/redispatch"
    )

    assert redispatch.status_code == 200
    assert redispatch.json()["status"] == "approved"
    assert redispatch.json()["job"]["id"] == job_id
    assert redispatch.json()["job"]["status"] == "queued"
    assert enqueued == [job_id, job_id]
    with test_sessions() as db:
        assert len(db.scalars(select(Job)).all()) == 1
        assert [
            event.action
            for event in db.scalars(
                select(AgentAudit)
                .where(AgentAudit.proposal_id == proposal["id"])
                .order_by(AgentAudit.id)
            )
        ] == [
            "proposed",
            "approved",
            "dispatch_pending",
            "redispatch_requested",
            "redispatched",
        ]


def test_authorized_rejection_is_audited_without_dispatch(agent_api):
    client, state, _adapter, enqueued, test_sessions = agent_api
    project_id = create_project(client)
    proposal = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "make a render"},
    ).json()
    add_reviewer = client.put(
        f"/v1/projects/{project_id}/members/reviewer",
        json={"role": "reviewer"},
    )
    assert add_reviewer.status_code == 200
    state["actor"] = Actor(name="reviewer", roles=["viewer"], user_id="reviewer-id")
    rejected = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["job"] is None
    assert enqueued == []
    with test_sessions() as db:
        assert db.get(AgentProposal, proposal["id"]).status == "rejected"
        assert [
            event.action
            for event in db.scalars(select(AgentAudit).order_by(AgentAudit.id))
        ] == ["proposed", "rejected"]


def test_agent_disabled_by_default_does_not_call_adapter(agent_api, monkeypatch):
    client, _, adapter, _, test_sessions = agent_api
    project_id = create_project(client)
    monkeypatch.setattr(api, "get_settings", lambda: Settings(agents_enabled=False))

    response = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "synthetic task"},
    )
    assert response.status_code == 503
    assert adapter.calls == []
    with test_sessions() as db:
        assert db.scalars(select(AgentProposal)).all() == []


def test_direct_ollama_request_without_lease_fails_closed(monkeypatch):
    from app import inference_coordination as ic

    if ic._active_lease is not None:
        ic._active_lease.set(None)
    adapter = agents.LocalOllamaAdapter("http://ollama:11434", "qwen3-vl:4b", "a" * 64)
    with pytest.raises(ic.InferenceCoordinationError, match="inference_coordination_required"):
        adapter._request("GET", "/api/tags")


def test_direct_ollama_request_with_lost_lease_fails_closed(monkeypatch):
    from app import inference_coordination as ic

    lease = ic.InferenceLease(owner="owner")
    token = ic._set_active_lease(lease)
    monkeypatch.setattr(
        ic, "ensure_lease_owned",
        lambda *_a, **_k: (_ for _ in ()).throw(ic.LeaseLostError("inference_lease_lost")),
    )
    try:
        adapter = agents.LocalOllamaAdapter("http://ollama:11434", "qwen3-vl:4b", "a" * 64)
        with pytest.raises(ic.LeaseLostError, match="inference_lease_lost|lease_lost"):
            adapter._request("GET", "/api/tags")
    finally:
        ic._reset_active_lease(token)
