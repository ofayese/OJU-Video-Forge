"""Mocked regression tests for deterministic step coverage in training text generation.

Tests verify that every approved procedure step is represented exactly once in the
generated training draft, using mocked Ollama responses (no real inference).
"""

import json

import httpx
import pytest

from app import agents
from app import inference_coordination as ic
from app.agents import AgentUnavailable, LocalOllamaAdapter


@pytest.fixture(autouse=True)
def _allow_direct_adapter_inference(monkeypatch):
    monkeypatch.setattr(ic, "ensure_lease_owned", lambda *_a, **_k: None)
    token = ic._set_active_lease(ic.InferenceLease(owner="test-agent-adapter"))
    try:
        yield
    finally:
        ic._reset_active_lease(token)


def _make_adapter(handler, monkeypatch):
    client = httpx.Client
    monkeypatch.setattr(
        agents.httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return LocalOllamaAdapter(
        "http://localhost:11434",
        "qwen2.5:7b-instruct",
        "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
        allow_host_endpoint=True,
    )


def _base_handler(monkeypatch, stage_outputs, prompt_stages, extra_paths=None):
    extra_paths = extra_paths or {}
    call_counts: dict[str, int] = {}

    def handler(request):
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b-instruct",
                            "digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
                        }
                    ]
                },
            )
        if path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion"],
                    "thinking": {"values": [False, True], "default": True},
                },
            )
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b-instruct",
                            "size": 1000000,
                            "size_vram": 0,
                        },
                    ]
                },
            )
        if path in extra_paths:
            return extra_paths[path](request)
        if path == "/api/chat":
            payload = json.loads(request.content)
            system = payload["messages"][0]["content"]
            stage_name = next(
                name for marker, name in prompt_stages.items() if marker in system
            )
            if stage_name == "narration":
                narration_output = stage_outputs["narration"]
                if "step_id" in narration_output:
                    output = narration_output  # Single-step narration output
                else:
                    user_content = payload["messages"][1]["content"]
                    user_payload = json.loads(user_content)
                    step_id = user_payload["step"]["step_id"]
                    output = narration_output[step_id]
            elif stage_name == "procedure" and isinstance(
                stage_outputs.get("procedure"), list
            ):
                idx = call_counts.get("procedure", 0)
                call_counts["procedure"] = idx + 1
                batches = stage_outputs["procedure"]
                output = batches[min(idx, len(batches) - 1)]
            else:
                output = stage_outputs[stage_name]
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 120,
                    "eval_count": 24,
                    "total_duration": 1_000_000_000,
                    "message": {"content": json.dumps(output), "thinking": ""},
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    return handler


def _base_evidence(steps):
    frames = []
    for i, step in enumerate(steps):
        frames.append(
            {
                "id": f"frame-{i}",
                "timestamp_ms": step["start_ms"],
                "ocr": step.get("ocr", f"Frame {i} content"),
            }
        )
    return {
        "duration_ms": steps[-1]["end_ms"],
        "approved_steps": steps,
        "frames": frames,
        "request": {"learner_level": "beginner", "objectives": "", "feedback": ""},
    }


def _step(step_id, start_ms, end_ms, description, evidence_ids=None, ocr=None):
    return {
        "id": step_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "description": description,
        "evidence": evidence_ids or [f"frame-{start_ms // 5000}"],
        "decision": "accepted",
        "confidence": "high",
        "evidence_category": "direct_observation",
    }


def _outline_output(step_ids, titles=None):
    titles = titles or {sid: f"Chapter for {sid}" for sid in step_ids}
    return {
        "title": "Test Training Outline",
        "audience": "beginner",
        "prerequisites": [],
        "learning_objectives": [
            {"text": f"Understand {sid}.", "step_ids": [sid]} for sid in step_ids
        ],
        "chapter_titles": [{"step_id": sid, "title": titles[sid]} for sid in step_ids],
    }


def _procedure_output(step_ids, evidence_map=None):
    evidence_map = evidence_map or {
        sid: [f"frame-{i}"] for i, sid in enumerate(step_ids)
    }
    return {
        "steps": [
            {"step_id": sid, "evidence_ids": evidence_map[sid]} for sid in step_ids
        ],
    }


def _narration_output(step_id, text=None):
    return {
        "step_id": step_id,
        "text": text or f"Narration for {step_id}.",
    }


def _activities_output(step_ids):
    return {
        "exercises": step_ids[:1],
        "validation_checkpoints": step_ids[1:2] or step_ids[:1],
        "troubleshooting": step_ids[2:3] or step_ids[:1],
        "assessment": step_ids[3:4] or step_ids[:1],
    }


# ---------------------------------------------------------------------------
# Test 1: Single-step success
# ---------------------------------------------------------------------------
def test_single_step_success(monkeypatch):
    steps = [_step("step-1", 0, 5000, "Observe the blue screen.", ["frame-0"])]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    stage_outputs = {
        "outline": _outline_output(["step-1"]),
        "procedure": _procedure_output(["step-1"]),
        "narration": _narration_output("step-1"),
        "activities": _activities_output(["step-1"]),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    result = adapter.generate_training(evidence, lambda: None)

    assert result.draft.title == "Test Training Outline"
    assert len(result.draft.chapters) == 1
    assert result.draft.chapters[0].id == "step-1"
    assert len(result.draft.script) == 1
    assert result.draft.script[0].id == "script-step-1"
    assert len(result.stages) == 4
    assert all(s["evidence_validation_result"] == "passed" for s in result.stages)


# ---------------------------------------------------------------------------
# Test 2: Two-step complete success
# ---------------------------------------------------------------------------
def test_two_step_complete_success(monkeypatch):
    steps = [
        _step("step-1", 0, 5000, "Observe the blue screen.", ["frame-0"]),
        _step("step-2", 5000, 10000, "Record the error code.", ["frame-1"]),
    ]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    stage_outputs = {
        "outline": _outline_output(["step-1", "step-2"]),
        "procedure": _procedure_output(["step-1", "step-2"]),
        "narration": {
            "step-1": _narration_output("step-1"),
            "step-2": _narration_output("step-2"),
        },
        "activities": _activities_output(["step-1", "step-2"]),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    result = adapter.generate_training(evidence, lambda: None)

    assert len(result.draft.chapters) == 2
    assert [c.id for c in result.draft.chapters] == ["step-1", "step-2"]
    assert len(result.draft.script) == 2
    assert [s.id for s in result.draft.script] == ["script-step-1", "script-step-2"]
    assert len(result.stages) == 5  # outline + procedure + 2 narration + activities


# ---------------------------------------------------------------------------
# Test 3: Two-step omission — model omits step-2 in outline chapter_titles
# ---------------------------------------------------------------------------
def test_two_step_omission_rejected(monkeypatch):
    steps = [
        _step("step-1", 0, 5000, "Observe the blue screen.", ["frame-0"]),
        _step("step-2", 5000, 10000, "Record the error code.", ["frame-1"]),
    ]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    # Model only provides chapter title for step-1, omitting step-2
    stage_outputs = {
        "outline": {
            "title": "Test Training Outline",
            "audience": "beginner",
            "prerequisites": [],
            "learning_objectives": [
                {"text": "Understand step-1.", "step_ids": ["step-1"]},
            ],
            "chapter_titles": [
                {"step_id": "step-1", "title": "Chapter for step-1"},
            ],
        },
        "procedure": _procedure_output(["step-1", "step-2"]),
        "narration": {
            "step-1": _narration_output("step-1"),
            "step-2": _narration_output("step-2"),
        },
        "activities": _activities_output(["step-1", "step-2"]),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    with pytest.raises(AgentUnavailable, match="agent_generation_evidence_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 4: Three-to-five-step complete success
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("num_steps", [3, 4, 5])
def test_three_to_five_step_complete_success(monkeypatch, num_steps):
    steps = [
        _step(
            f"step-{i}", i * 5000, (i + 1) * 5000, f"Step {i} action.", [f"frame-{i}"]
        )
        for i in range(num_steps)
    ]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    step_ids = [f"step-{i}" for i in range(num_steps)]

    # Procedure is batched in groups of 4; provide per-batch outputs with correct evidence refs
    proc_batches = []
    for offset in range(0, len(step_ids), 4):
        batch_ids = step_ids[offset : offset + 4]
        ev_map = {sid: [f"frame-{i}"] for i, sid in enumerate(batch_ids, start=offset)}
        proc_batches.append(_procedure_output(batch_ids, evidence_map=ev_map))

    stage_outputs = {
        "outline": _outline_output(step_ids),
        "procedure": proc_batches
        if len(proc_batches) > 1
        else _procedure_output(step_ids),
        "narration": {sid: _narration_output(sid) for sid in step_ids},
        "activities": _activities_output(step_ids),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    result = adapter.generate_training(evidence, lambda: None)

    assert len(result.draft.chapters) == num_steps
    assert [c.id for c in result.draft.chapters] == step_ids
    assert len(result.draft.script) == num_steps
    assert [s.id for s in result.draft.script] == [f"script-{sid}" for sid in step_ids]


# ---------------------------------------------------------------------------
# Test 5: Duplicate step ID — rejected at evidence validation
# ---------------------------------------------------------------------------
def test_duplicate_step_rejected(monkeypatch):
    steps = [
        _step("step-1", 0, 5000, "First occurrence.", ["frame-0"]),
        _step("step-1", 5000, 10000, "Duplicate step ID.", ["frame-1"]),
    ]
    evidence = _base_evidence(steps)

    adapter = _make_adapter(
        _base_handler(
            monkeypatch,
            {
                "outline": _outline_output(["step-1", "step-1"]),
                "procedure": _procedure_output(["step-1", "step-1"]),
                "narration": _narration_output("step-1"),
                "activities": _activities_output(["step-1"]),
            },
            {
                "Create a concise training outline metadata": "outline",
                "Map each supplied approved step": "procedure",
                "Write one short spoken narration segment for the supplied approved step": "narration",
                "Select approved step IDs": "activities",
            },
        ),
        monkeypatch,
    )
    with pytest.raises(AgentUnavailable, match="training_generation_evidence_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 6: Unknown step ID in learning objectives
# ---------------------------------------------------------------------------
def test_unknown_step_in_objectives_rejected(monkeypatch):
    steps = [_step("step-1", 0, 5000, "Observe the blue screen.", ["frame-0"])]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    # Model references step-99 which is not in approved steps
    stage_outputs = {
        "outline": {
            "title": "Test Training Outline",
            "audience": "beginner",
            "prerequisites": [],
            "learning_objectives": [
                {"text": "Understand step-99.", "step_ids": ["step-99"]},
            ],
            "chapter_titles": [
                {"step_id": "step-1", "title": "Chapter for step-1"},
            ],
        },
        "procedure": _procedure_output(["step-1"]),
        "narration": _narration_output("step-1"),
        "activities": _activities_output(["step-1"]),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    with pytest.raises(AgentUnavailable, match="agent_generation_evidence_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 7: Out-of-order chapter_titles
# ---------------------------------------------------------------------------
def test_out_of_order_chapter_titles_rejected(monkeypatch):
    steps = [
        _step("step-1", 0, 5000, "First step.", ["frame-0"]),
        _step("step-2", 5000, 10000, "Second step.", ["frame-1"]),
    ]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    # Model returns chapter_titles in wrong order
    stage_outputs = {
        "outline": _outline_output(["step-2", "step-1"]),
        "procedure": _procedure_output(["step-1", "step-2"]),
        "narration": {
            "step-1": _narration_output("step-1"),
            "step-2": _narration_output("step-2"),
        },
        "activities": _activities_output(["step-1", "step-2"]),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    with pytest.raises(AgentUnavailable, match="agent_generation_evidence_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 8: Missing narration segment for a step
# ---------------------------------------------------------------------------
def test_missing_narration_segment_rejected(monkeypatch):
    steps = [
        _step("step-1", 0, 5000, "First step.", ["frame-0"]),
        _step("step-2", 5000, 10000, "Second step.", ["frame-1"]),
    ]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }

    def handler(request):
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            system = payload["messages"][0]["content"]
            stage_name = next(
                name for marker, name in prompt_stages.items() if marker in system
            )
            if stage_name == "narration":
                # Always return step-1 narration, missing step-2
                output = _narration_output("step-1")
            else:
                outputs = {
                    "outline": _outline_output(["step-1", "step-2"]),
                    "procedure": _procedure_output(["step-1", "step-2"]),
                    "activities": _activities_output(["step-1", "step-2"]),
                }
                output = outputs[stage_name]
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 120,
                    "eval_count": 24,
                    "total_duration": 1_000_000_000,
                    "message": {"content": json.dumps(output), "thinking": ""},
                },
            )
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b-instruct",
                            "digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
                        }
                    ]
                },
            )
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion"],
                    "thinking": {"values": [False, True], "default": True},
                },
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b-instruct",
                            "size": 1000000,
                            "size_vram": 0,
                        },
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client
    monkeypatch.setattr(
        agents.httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    adapter = LocalOllamaAdapter(
        "http://localhost:11434",
        "qwen2.5:7b-instruct",
        "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
        allow_host_endpoint=True,
    )
    with pytest.raises(AgentUnavailable, match="agent_generation_evidence_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 9: Stale evidence revision — evidence timestamps don't match
# ---------------------------------------------------------------------------
def test_stale_evidence_revision_rejected(monkeypatch):
    steps = [
        _step("step-1", 0, 5000, "Observe the screen.", ["frame-0"]),
    ]
    evidence = {
        "duration_ms": 5000,
        "approved_steps": steps,
        "frames": [
            {"id": "frame-0", "timestamp_ms": 99999, "ocr": "Stale frame"},
        ],
        "request": {"learner_level": "beginner", "objectives": "", "feedback": ""},
    }

    adapter = _make_adapter(
        _base_handler(
            monkeypatch,
            {
                "outline": _outline_output(["step-1"]),
                "procedure": _procedure_output(["step-1"]),
                "narration": _narration_output("step-1"),
                "activities": _activities_output(["step-1"]),
            },
            {
                "Create a concise training outline metadata": "outline",
                "Map each supplied approved step": "procedure",
                "Write one short spoken narration segment for the supplied approved step": "narration",
                "Select approved step IDs": "activities",
            },
        ),
        monkeypatch,
    )
    with pytest.raises(AgentUnavailable, match="training_generation_evidence_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 10: Invalid timestamps (end before start)
# ---------------------------------------------------------------------------
def test_invalid_timestamps_rejected(monkeypatch):
    steps = [
        _step("step-1", 5000, 0, "End before start.", ["frame-0"]),
    ]
    evidence = _base_evidence(steps)

    adapter = _make_adapter(
        _base_handler(
            monkeypatch,
            {
                "outline": _outline_output(["step-1"]),
                "procedure": _procedure_output(["step-1"]),
                "narration": _narration_output("step-1"),
                "activities": _activities_output(["step-1"]),
            },
            {
                "Create a concise training outline metadata": "outline",
                "Map each supplied approved step": "procedure",
                "Write one short spoken narration segment for the supplied approved step": "narration",
                "Select approved step IDs": "activities",
            },
        ),
        monkeypatch,
    )
    with pytest.raises(AgentUnavailable, match="training_generation_evidence_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 11: Partial failure and cancellation
# ---------------------------------------------------------------------------
def test_cancellation_during_generation(monkeypatch):
    steps = [_step("step-1", 0, 5000, "Observe the screen.", ["frame-0"])]
    evidence = _base_evidence(steps)

    call_count = {"n": 0}

    def check():
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise RuntimeError("cancelled")

    adapter = _make_adapter(
        _base_handler(
            monkeypatch,
            {
                "outline": _outline_output(["step-1"]),
                "procedure": _procedure_output(["step-1"]),
                "narration": _narration_output("step-1"),
                "activities": _activities_output(["step-1"]),
            },
            {
                "Create a concise training outline metadata": "outline",
                "Map each supplied approved step": "procedure",
                "Write one short spoken narration segment for the supplied approved step": "narration",
                "Select approved step IDs": "activities",
            },
        ),
        monkeypatch,
    )
    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.generate_training(evidence, check)


# ---------------------------------------------------------------------------
# Test 12: Retry exhaustion — model keeps returning invalid outline metadata
# ---------------------------------------------------------------------------
def test_retry_exhaustion(monkeypatch):
    steps = [_step("step-1", 0, 5000, "Observe the screen.", ["frame-0"])]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    # Model always returns chapter_titles missing step-1
    stage_outputs = {
        "outline": {
            "title": "Test Training Outline",
            "audience": "beginner",
            "prerequisites": [],
            "learning_objectives": [
                {"text": "Understand step-1.", "step_ids": ["step-1"]},
            ],
            "chapter_titles": [],  # Empty — missing step-1
        },
        "procedure": _procedure_output(["step-1"]),
        "narration": _narration_output("step-1"),
        "activities": _activities_output(["step-1"]),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    with pytest.raises(AgentUnavailable, match="agent_generation_schema_invalid"):
        adapter.generate_training(evidence, lambda: None)


# ---------------------------------------------------------------------------
# Test 13: Deterministic assembly — same input produces same output
# ---------------------------------------------------------------------------
def test_deterministic_assembly(monkeypatch):
    steps = [
        _step("step-1", 0, 5000, "First step.", ["frame-0"]),
        _step("step-2", 5000, 10000, "Second step.", ["frame-1"]),
    ]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    stage_outputs = {
        "outline": _outline_output(["step-1", "step-2"]),
        "procedure": _procedure_output(["step-1", "step-2"]),
        "narration": {
            "step-1": _narration_output("step-1"),
            "step-2": _narration_output("step-2"),
        },
        "activities": _activities_output(["step-1", "step-2"]),
    }

    handler = _base_handler(monkeypatch, stage_outputs, prompt_stages)
    client = httpx.Client
    monkeypatch.setattr(
        agents.httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    adapter = LocalOllamaAdapter(
        "http://localhost:11434",
        "qwen2.5:7b-instruct",
        "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
        allow_host_endpoint=True,
    )

    result1 = adapter.generate_training(evidence, lambda: None)
    result2 = adapter.generate_training(evidence, lambda: None)

    assert result1.draft.title == result2.draft.title
    assert [c.id for c in result1.draft.chapters] == [
        c.id for c in result2.draft.chapters
    ]
    assert [s.id for s in result1.draft.script] == [s.id for s in result2.draft.script]


# ---------------------------------------------------------------------------
# Test 14: Pending human-review status — draft not auto-approved
# ---------------------------------------------------------------------------
def test_pending_human_review_status(monkeypatch):
    steps = [_step("step-1", 0, 5000, "Observe the screen.", ["frame-0"])]
    evidence = _base_evidence(steps)

    prompt_stages = {
        "Create a concise training outline metadata": "outline",
        "Map each supplied approved step": "procedure",
        "Write one short spoken narration segment for the supplied approved step": "narration",
        "Select approved step IDs": "activities",
    }
    stage_outputs = {
        "outline": _outline_output(["step-1"]),
        "procedure": _procedure_output(["step-1"]),
        "narration": _narration_output("step-1"),
        "activities": _activities_output(["step-1"]),
    }

    adapter = _make_adapter(
        _base_handler(monkeypatch, stage_outputs, prompt_stages), monkeypatch
    )
    result = adapter.generate_training(evidence, lambda: None)

    # Draft is generated but not auto-approved — no review_status field exists;
    # generation returns a draft that requires separate human review approval.
    assert result.draft.title == "Test Training Outline"
    assert len(result.draft.chapters) == 1
    assert result.draft.chapters[0].id == "step-1"
