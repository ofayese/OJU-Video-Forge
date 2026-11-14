import pytest

from app.training import TrainingDraft, validate_procedure, validate_training_draft


def step(**updates):
    return {
        "id": "step-1",
        "start_ms": 0,
        "end_ms": 5000,
        "description": "Open the visible menu",
        "evidence": ["frame-1"],
        "uncertainty": ["click", "keyboard", "intent"],
        "evidence_category": "unverified",
        "confidence": "unassessed",
        "decision": "pending",
        **updates,
    }


def test_unresolved_inferences_cannot_be_approved():
    pending = step()
    assert validate_procedure([pending], {"frame-1"}, 5000)[0]["decision"] == "pending"
    with pytest.raises(ValueError, match="unreviewed"):
        validate_procedure([pending], {"frame-1"}, 5000, approved=True)
    reviewed = step(decision="accepted", confidence="medium")
    assert validate_procedure([reviewed], {"frame-1"}, 5000, approved=True)[0] == reviewed


@pytest.mark.parametrize("candidate", [
    step(evidence=["unknown"], decision="accepted"),
    step(description=" ", decision="accepted"),
    step(end_ms=5001, decision="accepted"),
    step(decision="rejected"),
    step(decision="accepted", confidence="unassessed"),
    step(decision="accepted", confidence="low", evidence_category="missing"),
])
def test_approval_rejects_unbacked_or_unusable_procedure(candidate):
    with pytest.raises(ValueError):
        validate_procedure([candidate], {"frame-1"}, 5000, approved=True)


def test_training_draft_defaults_omitted_optional_sections():
    claim = {"text": "Identify the visible label.", "kind": "evidence", "evidence": ["frame-0"]}
    timed = {"id": "segment-1", "start_ms": 0, "end_ms": 1000, **claim}
    draft = validate_training_draft(
        {
            "title": "Reading a visible label",
            "audience": "Training reviewers",
            "learning_objectives": [claim],
            "chapters": [timed],
            "script": [timed],
        },
        {"frame-0"},
        1000,
        [{"start_ms": 0, "end_ms": 1000, "evidence": ["frame-0"]}],
    )

    optional_sections = (
        "prerequisites", "annotations", "technical_explanations", "lab_steps",
        "validation_checkpoints", "troubleshooting", "assessment", "answer_explanations",
    )
    assert all(getattr(draft, section) == [] for section in optional_sections)
    assert not set(optional_sections) & set(TrainingDraft.model_json_schema()["required"])
