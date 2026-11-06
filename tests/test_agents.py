import pytest
from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import agents, api, auth
from app.auth import Actor
from app.config import Settings
from app.core import AgentAudit, AgentProposal, Base, Job, ProjectMembership


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


@pytest.fixture
def agent_api(tmp_path, monkeypatch):
    database = create_engine(
        f"sqlite:///{tmp_path / 'agents.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    test_sessions = sessionmaker(database, expire_on_commit=False)
    state = {"actor": Actor(name="owner", roles=["producer"])}
    adapter = FakeAgentAdapter()
    enqueued = []

    def session_override():
        with test_sessions() as db:
            yield db

    class Queue:
        def enqueue(self, *args, **kwargs):
            enqueued.append((args, kwargs))

    monkeypatch.setattr(api, "get_settings", lambda: Settings(agents_enabled=True))
    monkeypatch.setattr(api, "get_agent_adapter", lambda: adapter)
    monkeypatch.setattr(api, "queue", lambda: Queue())
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

    state["actor"] = Actor(name="owner", roles=["producer", "reviewer"])
    self_approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/approve"
    )
    assert self_approval.status_code == 403
    assert enqueued == []

    state["actor"] = Actor(name="owner", roles=["producer"])
    add_reviewer = client.put(
        f"/v1/projects/{project_id}/members",
        json={"actor": "reviewer", "role": "reviewer"},
    )
    assert add_reviewer.status_code == 200
    assert add_reviewer.json() == {"actor": "reviewer", "role": "reviewer"}
    state["actor"] = Actor(name="reviewer", roles=["reviewer"])
    approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/approve"
    )
    assert approval.status_code == 200
    assert approval.json()["status"] == "approved"
    assert approval.json()["job"]["operation"] == "render_test"
    assert len(enqueued) == 1
    assert enqueued[0][0] == ("app.worker.execute", approval.json()["job"]["id"])

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
    client, state, adapter, enqueued, test_sessions = agent_api
    project_id = create_project(client)
    response = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "make a render"},
    )
    proposal_id = response.json()["id"]

    outsider = Actor(name="outsider", roles=["producer"])
    state["actor"] = outsider
    hidden = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "cross-project request"},
    )
    assert hidden.status_code == 404

    state["actor"] = Actor(name="outsider", roles=["producer"])
    outsider_project = create_project(client, "Other synthetic project")
    state["actor"] = Actor(name="owner", roles=["producer"])
    cross_project = client.post(
        f"/v1/projects/{outsider_project}/agent/proposals",
        json={"task": "cross-project request"},
    )
    assert cross_project.status_code == 404

    with test_sessions() as db:
        db.add(
            ProjectMembership(project_id=project_id, actor="reviewer", role="reviewer")
        )
        db.commit()
    state["actor"] = Actor(
        name="reviewer", roles=["reviewer", "agent_service"]
    )
    service_approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal_id}/approve"
    )
    assert service_approval.status_code == 403
    assert enqueued == []


def test_malformed_agent_output_is_not_persisted(agent_api, caplog):
    client, state, adapter, _, test_sessions = agent_api
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
        assert db.get(ProjectMembership, (project_id, "owner")) is not None


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
    assert len(enqueued) == 1
    assert enqueued[0][0] == ("app.worker.execute", response.json()["id"])
    with test_sessions() as db:
        assert db.get(Job, response.json()["id"]).status == "queued"


def test_approved_dispatch_pending_job_can_be_redispatched(agent_api, monkeypatch):
    client, state, _, enqueued, test_sessions = agent_api
    project_id = create_project(client)
    add_editor = client.put(
        f"/v1/projects/{project_id}/members",
        json={"actor": "editor", "role": "editor"},
    )
    assert add_editor.status_code == 200
    state["actor"] = Actor(name="editor", roles=["producer"])
    proposal = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "make a render"},
    ).json()
    state["actor"] = Actor(name="owner", roles=["producer"])

    class FailingQueue:
        def enqueue(self, *args, **kwargs):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(api, "queue", lambda: FailingQueue())
    approval = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/approve"
    )
    assert approval.status_code == 200
    job_id = approval.json()["job"]["id"]
    assert approval.json()["job"]["status"] == "dispatch_pending"

    class RecoveringQueue:
        def enqueue(self, *args, **kwargs):
            enqueued.append((args, kwargs))

    monkeypatch.setattr(api, "queue", lambda: RecoveringQueue())
    state["actor"] = Actor(name="owner", roles=["producer"])
    redispatch = client.post(
        f"/v1/projects/{project_id}/agent/proposals/{proposal['id']}/redispatch"
    )

    assert redispatch.status_code == 200
    assert redispatch.json()["status"] == "approved"
    assert redispatch.json()["job"]["id"] == job_id
    assert redispatch.json()["job"]["status"] == "queued"
    assert enqueued == [
        (("app.worker.execute", job_id), {"job_id": job_id, "job_timeout": 3600})
    ]
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
    client, state, adapter, enqueued, test_sessions = agent_api
    project_id = create_project(client)
    proposal = client.post(
        f"/v1/projects/{project_id}/agent/proposals",
        json={"task": "make a render"},
    ).json()
    add_reviewer = client.put(
        f"/v1/projects/{project_id}/members",
        json={"actor": "reviewer", "role": "reviewer"},
    )
    assert add_reviewer.status_code == 200
    state["actor"] = Actor(name="reviewer", roles=["reviewer"])
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
