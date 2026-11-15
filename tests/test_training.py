import pytest

from app.training import (
    LearningActivityPlan,
    NarrationBatch,
    ProcedureEvidenceBatch,
    TrainingDraft,
    TrainingOutline,
    TrainingOutlineChapter,
    TrainingOutlineObjective,
    approved_procedure_revision,
    assemble_training_draft,
    build_training_provenance,
    update_training_provenance,
    validate_procedure,
    validate_training_draft,
    validate_training_observations,
    validate_training_provenance,
)


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


def test_staged_training_assembly_uses_only_approved_evidence_and_intervals():
    approved = step(
        start_ms=1000, end_ms=3000, evidence=["frame-1"], decision="accepted",
        confidence="high", evidence_category="direct_observation",
    )
    frames = [{"id": "frame-1", "timestamp_ms": 1000, "ocr": "Menu"}]
    validate_training_observations([approved], frames, 5000)
    with pytest.raises(ValueError, match="outside_approved_interval"):
        validate_training_observations(
            [approved], [{**frames[0], "timestamp_ms": 3000}], 5000
        )

    outline = TrainingOutline(
        title="Review a visible menu",
        audience="Beginner",
        prerequisites=[],
        learning_objectives=[
            TrainingOutlineObjective(
                text="Identify the reviewed menu.", step_ids=["step-1"],
            ),
        ],
        chapters=[
            TrainingOutlineChapter(
                id="chapter-1", title="Identify the menu.", step_id="step-1",
            ),
        ],
    )
    procedure = [ProcedureEvidenceBatch.model_validate({
        "steps": [{"step_id": "step-1", "evidence_ids": ["frame-1"]}],
    })]
    narration = [NarrationBatch.model_validate({
        "segments": [{"step_id": "step-1", "text": "The reviewed menu is visible."}],
    })]
    activities = LearningActivityPlan(
        exercises=["step-1"],
        validation_checkpoints=["step-1"],
        troubleshooting=["step-1"],
        assessment=["step-1"],
    )
    draft = assemble_training_draft(
        outline, procedure, narration, activities, [approved], 5000,
    )
    assert draft.chapters[0].start_ms == 1000
    assert draft.chapters[0].end_ms == 3000
    assert draft.script[0].evidence == ["frame-1"]
    assert draft.troubleshooting[0].kind == "supplemental"
    assert draft.troubleshooting[0].evidence == []


def test_training_provenance_is_revision_bound_and_requires_explicit_review():
    frames = [
        {"id": "frame-1", "timestamp_ms": 1000},
        {"id": "frame-2", "timestamp_ms": 2000},
    ]
    draft = TrainingDraft.model_validate({
        "title": "Review a visible menu",
        "audience": "Beginner",
        "learning_objectives": [{
            "text": "Identify the reviewed menu.", "kind": "evidence", "evidence": ["frame-1"],
        }],
        "chapters": [{
            "id": "chapter-1", "start_ms": 1000, "end_ms": 3000,
            "text": "Identify the menu.", "kind": "evidence", "evidence": ["frame-1"],
        }],
        "script": [{
            "id": "script-1", "start_ms": 1000, "end_ms": 3000,
            "text": "The reviewed menu is visible.", "kind": "evidence", "evidence": ["frame-1"],
        }],
    })
    provider = {"provider": "ollama", "model": "qwen3-vl:4b", "model_digest": "a" * 64}
    provenance = build_training_provenance(
        draft, [{
            "stage": "outline", "schema_version": 1,
            "model_identifier": provider["model"],
            "model_digest": provider["model_digest"],
            "schema_validation_result": "passed",
            "evidence_validation_result": "passed",
        }], provider, frames, "b" * 64, 4, 3,
    )
    assert provenance["schema_version"] == 2
    validate_training_provenance(
        provenance, draft, provider, frames, "b" * 64, 3,
    )
    with pytest.raises(ValueError, match="identity_mismatch"):
        validate_training_provenance(
            provenance, draft, provider, frames, "c" * 64, 3,
        )
    with pytest.raises(ValueError, match="identity_mismatch"):
        validate_training_provenance(
            provenance, draft, provider, frames, "b" * 64, 4,
        )
    with pytest.raises(ValueError, match="identity_mismatch"):
        validate_training_provenance(
            provenance, draft, provider, frames, "b" * 64, 3,
            require_human_approved=True,
        )

    edited = draft.model_copy(update={"title": "Edited menu lesson"})
    updated = update_training_provenance(
        provenance, edited, provider, frames, "b" * 64, 3, "reviewer-id",
    )
    title = next(item for item in updated["elements"] if item["path"] == "title")
    assert title["generation_stage"] == "human_edit"
    assert title["edited_by"] == "reviewer-id"
    assert updated["review_status"] == "pending_human_review"
    validate_training_provenance(updated, edited, provider, frames, "b" * 64, 3)

    timing_edited = draft.model_copy(update={
        "script": [draft.script[0].model_copy(update={"end_ms": 2900})],
    })
    timing_updated = update_training_provenance(
        provenance, timing_edited, provider, frames, "b" * 64, 3, "reviewer-id",
    )
    script = next(item for item in timing_updated["elements"] if item["path"] == "script[0]")
    original_script = next(item for item in provenance["elements"] if item["path"] == "script[0]")
    assert script["generation_stage"] == "human_edit"
    assert script["edited_by"] == "reviewer-id"
    assert script["parent_element_sha256"] == original_script["element_sha256"]
    validate_training_provenance(timing_updated, timing_edited, provider, frames, "b" * 64, 3)

    evidence_edited = draft.model_copy(update={
        "script": [draft.script[0].model_copy(update={"evidence": ["frame-2"]})],
    })
    evidence_updated = update_training_provenance(
        provenance, evidence_edited, provider, frames, "b" * 64, 3, "reviewer-id",
    )
    script = next(item for item in evidence_updated["elements"] if item["path"] == "script[0]")
    assert script["generation_stage"] == "human_edit"
    assert script["edited_by"] == "reviewer-id"
    validate_training_provenance(evidence_updated, evidence_edited, provider, frames, "b" * 64, 3)

    legacy = {
        **provenance,
        "schema_version": 1,
        "elements": [
            {
                **{key: value for key, value in item.items() if key != "element_sha256"},
                "schema_version": 1,
            }
            for item in provenance["elements"]
        ],
    }
    validate_training_provenance(legacy, draft, provider, frames, "b" * 64, 3)
    migrated = update_training_provenance(
        legacy, timing_edited, provider, frames, "b" * 64, 3, "reviewer-id",
    )
    assert migrated["schema_version"] == 2
    script = next(item for item in migrated["elements"] if item["path"] == "script[0]")
    assert script["generation_stage"] == "human_edit"
    assert script["schema_version"] == 2
    validate_training_provenance(migrated, timing_edited, provider, frames, "b" * 64, 3)


def test_approved_procedure_revision_requires_review_history():
    assert approved_procedure_revision({
        "status": "approved",
        "review_history": [{"revision": 7}],
    }) == 7
    with pytest.raises(ValueError, match="unavailable"):
        approved_procedure_revision({"status": "approved", "review_history": []})
