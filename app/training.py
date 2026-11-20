"""Evidence review primitives for silent-recording training workflows."""

import hashlib
import html
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrainingOutlineObjective(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=500)
    step_ids: list[str] = Field(min_length=1, max_length=20)


class TrainingOutlineChapter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    title: str = Field(min_length=1, max_length=200)
    step_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")


class TrainingOutline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=500)
    prerequisites: list[str] = Field(max_length=20)
    learning_objectives: list[TrainingOutlineObjective] = Field(
        min_length=1, max_length=20
    )
    chapters: list[TrainingOutlineChapter] = Field(min_length=1, max_length=20)


class TrainingOutlineChapterTitle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    title: str = Field(min_length=1, max_length=200)


class TrainingOutlineMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=500)
    prerequisites: list[str] = Field(max_length=20)
    learning_objectives: list[TrainingOutlineObjective] = Field(
        min_length=1, max_length=20
    )
    chapter_titles: list[TrainingOutlineChapterTitle] = Field(
        min_length=1, max_length=20
    )


class ProcedureEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    evidence_ids: list[str] = Field(min_length=1, max_length=24)


class ProcedureEvidenceBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    steps: list[ProcedureEvidenceReference] = Field(min_length=1, max_length=4)


class NarrationText(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    text: str = Field(min_length=1, max_length=500)


class NarrationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    segments: list[NarrationText] = Field(min_length=1, max_length=4)


class LearningActivityPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    exercises: list[str] = Field(min_length=1, max_length=4)
    validation_checkpoints: list[str] = Field(min_length=1, max_length=4)
    troubleshooting: list[str] = Field(min_length=1, max_length=4)
    assessment: list[str] = Field(min_length=1, max_length=4)


@dataclass(frozen=True)
class TrainingGeneration:
    draft: "TrainingDraft"
    stages: list[dict]


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    step_type: Literal["linear", "decision", "branch", "join"] = "linear"
    start_ms: int = Field(strict=True, ge=0)
    end_ms: int = Field(strict=True, gt=0)
    description: str = Field(max_length=1000)
    evidence: list[str] = Field(min_length=1, max_length=24)
    uncertainty: list[Literal["click", "keyboard", "intent", "visual"]] = Field(
        default_factory=list, max_length=4
    )
    evidence_category: Literal[
        "direct_observation", "supported_inference", "unverified", "missing"
    ] = "unverified"
    confidence: Literal["unassessed", "low", "medium", "high"] = "unassessed"
    decision: Literal["pending", "accepted", "rejected"] = "pending"
    condition: str | None = Field(default=None, max_length=500)
    branch_targets: list[str] = Field(default_factory=list, max_length=8)
    default_target: str | None = Field(default=None, max_length=40)
    parent_decision: str | None = Field(default=None, max_length=40)


class TrainingClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["evidence", "supplemental"]
    evidence: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def evidence_required(self):
        if (
            not self.text.strip()
            or any(not char.isprintable() and char not in "\n\t" for char in self.text)
            or (self.kind == "evidence" and not self.evidence)
        ):
            raise ValueError("evidence_or_guidance_required")
        if self.kind == "supplemental" and self.evidence:
            raise ValueError("supplemental_guidance_must_not_claim_evidence")
        return self


class TeachingSegment(TrainingClaim):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    start_ms: int = Field(strict=True, ge=0)
    end_ms: int = Field(strict=True, gt=0)


class TrainingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=500)
    prerequisites: list[TrainingClaim] = Field(default_factory=list, max_length=20)
    learning_objectives: list[TrainingClaim] = Field(min_length=1, max_length=20)
    chapters: list[TeachingSegment] = Field(min_length=1, max_length=100)
    script: list[TeachingSegment] = Field(min_length=1, max_length=200)
    annotations: list[TeachingSegment] = Field(default_factory=list, max_length=200)
    technical_explanations: list[TrainingClaim] = Field(
        default_factory=list, max_length=100
    )
    lab_steps: list[TrainingClaim] = Field(default_factory=list, max_length=100)
    validation_checkpoints: list[TrainingClaim] = Field(
        default_factory=list, max_length=100
    )
    troubleshooting: list[TrainingClaim] = Field(default_factory=list, max_length=100)
    assessment: list[TrainingClaim] = Field(default_factory=list, max_length=100)
    answer_explanations: list[TrainingClaim] = Field(
        default_factory=list, max_length=100
    )


def validate_training_observations(
    approved_steps: list[dict], frames: list[dict], duration_ms: int
) -> dict[str, dict]:
    frame_by_id = {}
    for frame in frames:
        frame_id = frame.get("id")
        timestamp = frame.get("timestamp_ms")
        if (
            not isinstance(frame_id, str)
            or frame_id in frame_by_id
            or not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or not 0 <= timestamp <= duration_ms
        ):
            raise ValueError("invalid_training_observation")
        frame_by_id[frame_id] = frame

    validated = validate_procedure(
        approved_steps, set(frame_by_id), duration_ms, approved=True
    )
    for step in validated:
        for evidence_id in step["evidence"]:
            timestamp = frame_by_id[evidence_id]["timestamp_ms"]
            if not step["start_ms"] <= timestamp < step["end_ms"]:
                raise ValueError("training_observation_outside_approved_interval")
    return frame_by_id


def approved_procedure_revision(procedure: dict) -> int:
    history = procedure.get("review_history")
    if (
        procedure.get("status") != "approved"
        or not isinstance(history, list)
        or not history
        or not isinstance(history[-1], dict)
    ):
        raise ValueError("approved_procedure_revision_unavailable")
    revision = history[-1].get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("approved_procedure_revision_invalid")
    return revision


def _step_claim(
    text: str, step_ids: list[str], steps: dict[str, dict]
) -> TrainingClaim:
    evidence = list(
        dict.fromkeys(ref for step_id in step_ids for ref in steps[step_id]["evidence"])
    )
    return TrainingClaim(text=text, kind="evidence", evidence=evidence)


def assemble_training_draft(
    outline: TrainingOutline,
    procedure_batches: list[ProcedureEvidenceBatch],
    narration_batches: list[NarrationBatch],
    activities: LearningActivityPlan,
    approved_steps: list[dict],
    duration_ms: int,
) -> TrainingDraft:
    step_by_id = {step["id"]: step for step in approved_steps}
    expected_ids = list(step_by_id)
    if not expected_ids or len(expected_ids) > 20:
        raise ValueError("training_generation_step_limit")

    ordered_ids = []
    for batch in procedure_batches:
        batch_ids = [item.step_id for item in batch.steps]
        if (
            batch_ids
            != expected_ids[len(ordered_ids) : len(ordered_ids) + len(batch_ids)]
        ):
            raise ValueError("training_procedure_batch_mismatch")
        for item in batch.steps:
            if item.evidence_ids != step_by_id[item.step_id]["evidence"]:
                raise ValueError("training_procedure_evidence_mismatch")
        ordered_ids.extend(batch_ids)
    if ordered_ids != expected_ids:
        raise ValueError("training_procedure_incomplete")

    chapter_ids = [item.step_id for item in outline.chapters]
    chapter_names = [item.id for item in outline.chapters]
    if (
        chapter_ids != expected_ids
        or len(set(chapter_names)) != len(chapter_names)
        or any(
            not set(item.step_ids).issubset(step_by_id)
            for item in outline.learning_objectives
        )
    ):
        raise ValueError("training_outline_evidence_mismatch")

    segments = {}
    for batch in narration_batches:
        for segment in batch.segments:
            if segment.step_id in segments or segment.step_id not in step_by_id:
                raise ValueError("training_narration_step_mismatch")
            segments[segment.step_id] = segment.text
    if list(segments) != expected_ids:
        raise ValueError("training_narration_incomplete")

    activity_refs = (
        activities.exercises
        + activities.validation_checkpoints
        + activities.troubleshooting
        + activities.assessment
    )
    if any(step_id not in step_by_id for step_id in activity_refs):
        raise ValueError("training_activity_evidence_mismatch")
    for values in (
        activities.exercises,
        activities.validation_checkpoints,
        activities.troubleshooting,
        activities.assessment,
    ):
        if len(set(values)) != len(values):
            raise ValueError("training_activity_duplicate_step")

    chapters = []
    scripts = []
    for chapter in outline.chapters:
        step = step_by_id[chapter.step_id]
        chapters.append(
            {
                "id": chapter.id,
                "start_ms": step["start_ms"],
                "end_ms": step["end_ms"],
                "text": chapter.title,
                "kind": "evidence",
                "evidence": step["evidence"],
            }
        )
    for step_id in expected_ids:
        step = step_by_id[step_id]
        scripts.append(
            {
                "id": f"script-{step_id}",
                "start_ms": step["start_ms"],
                "end_ms": step["end_ms"],
                "text": segments[step_id],
                "kind": "evidence",
                "evidence": step["evidence"],
            }
        )

    def selected(ids: list[str], text: str) -> list[dict]:
        return [
            _step_claim(
                text.format(description=step_by_id[step_id]["description"]),
                [step_id],
                step_by_id,
            ).model_dump()
            for step_id in ids
        ]

    draft = TrainingDraft.model_validate(
        {
            "title": outline.title,
            "audience": outline.audience,
            "prerequisites": [
                {"text": item, "kind": "supplemental", "evidence": []}
                for item in outline.prerequisites
            ],
            "learning_objectives": [
                _step_claim(item.text, item.step_ids, step_by_id).model_dump()
                for item in outline.learning_objectives
            ],
            "chapters": chapters,
            "script": scripts,
            "annotations": [],
            "technical_explanations": [],
            "lab_steps": selected(
                activities.exercises, "Practice only the reviewed action: {description}"
            ),
            "validation_checkpoints": selected(
                activities.validation_checkpoints,
                "Check that the cited evidence supports the reviewed action: {description}",
            ),
            "troubleshooting": [
                {
                    "text": "If the reviewed action cannot be confirmed, pause and request human review.",
                    "kind": "supplemental",
                    "evidence": [],
                }
                for _ in activities.troubleshooting
            ],
            "assessment": selected(
                activities.assessment, "What reviewed action is shown: {description}?"
            ),
            "answer_explanations": selected(
                activities.assessment,
                "The reviewed procedure describes: {description}",
            ),
        }
    )
    validate_training_draft(
        draft.model_dump(),
        {ref for step in approved_steps for ref in step["evidence"]},
        duration_ms,
        approved_steps,
    )
    return draft


def _training_elements(
    draft: TrainingDraft,
) -> list[tuple[str, str, list[str], str, dict]]:
    elements = [
        ("title", draft.title, [], "outline", {"text": draft.title}),
        ("audience", draft.audience, [], "outline", {"text": draft.audience}),
    ]
    for section, stage in (
        ("prerequisites", "outline"),
        ("learning_objectives", "outline"),
        ("chapters", "outline"),
        ("script", "narration"),
        ("annotations", "narration"),
        ("technical_explanations", "activities"),
        ("lab_steps", "activities"),
        ("validation_checkpoints", "activities"),
        ("troubleshooting", "activities"),
        ("assessment", "activities"),
        ("answer_explanations", "activities"),
    ):
        for index, item in enumerate(getattr(draft, section)):
            data = item.model_dump(mode="json")
            elements.append(
                (
                    f"{section}[{index}]",
                    data["text"],
                    data.get("evidence", []),
                    stage,
                    data,
                )
            )
    return elements


def build_training_provenance(
    draft: TrainingDraft,
    stages: list[dict],
    provider: dict,
    frames: list[dict],
    source_video_sha256: str,
    source_revision: int,
    approved_procedure_revision: int,
) -> dict:
    frame_by_id = {item["id"]: item["timestamp_ms"] for item in frames}
    elements = []
    for path, text, evidence_ids, generation_stage, data in _training_elements(draft):
        elements.append(
            {
                "path": path,
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "element_sha256": draft_digest({"path": path, "data": data}),
                "source_video_sha256": source_video_sha256,
                "evidence_ids": evidence_ids,
                "source_timestamps_ms": [
                    {"evidence_id": ref, "timestamp_ms": frame_by_id[ref]}
                    for ref in evidence_ids
                ],
                "approved_procedure_revision": approved_procedure_revision,
                "provider": provider,
                "generation_stage": generation_stage,
                "schema_version": 2,
                "review_status": "pending_human_review",
            }
        )
    return {
        "schema_version": 2,
        "source_video_sha256": source_video_sha256,
        "source_revision": source_revision,
        "approved_procedure_revision": approved_procedure_revision,
        "provider": provider,
        "review_status": "pending_human_review",
        "stages": stages,
        "elements": elements,
    }


def validate_training_provenance(
    provenance: dict,
    draft: TrainingDraft,
    provider: dict,
    frames: list[dict],
    source_video_sha256: str,
    approved_procedure_revision: int,
    *,
    require_human_approved: bool = False,
) -> None:
    expected_status = "human_approved" if require_human_approved else None
    schema_version = provenance.get("schema_version")
    source_revision = provenance.get("source_revision")
    if (
        type(schema_version) is not int
        or schema_version not in {1, 2}
        or provenance.get("source_video_sha256") != source_video_sha256
        or not isinstance(source_revision, int)
        or source_revision < 1
        or provenance.get("approved_procedure_revision") != approved_procedure_revision
        or provenance.get("provider") != provider
        or provenance.get("review_status")
        not in {
            "pending_human_review",
            "human_approved",
        }
        or (expected_status and provenance.get("review_status") != expected_status)
    ):
        raise ValueError("training_provenance_identity_mismatch")
    try:
        frame_by_id = {item["id"]: item["timestamp_ms"] for item in frames}
    except (KeyError, TypeError) as exc:
        raise ValueError("training_provenance_observations_invalid") from exc
    elements = provenance.get("elements")
    if not isinstance(elements, list) or len(elements) != len(
        _training_elements(draft)
    ):
        raise ValueError("training_provenance_elements_mismatch")
    by_path = {item.get("path"): item for item in elements if isinstance(item, dict)}
    if len(by_path) != len(elements):
        raise ValueError("training_provenance_elements_mismatch")
    for path, text, evidence_ids, _, data in _training_elements(draft):
        item = by_path.get(path)
        expected_timestamps = [
            {"evidence_id": ref, "timestamp_ms": frame_by_id[ref]}
            for ref in evidence_ids
            if ref in frame_by_id
        ]
        if (
            item is None
            or item.get("content_sha256")
            != hashlib.sha256(text.encode("utf-8")).hexdigest()
            or item.get("source_video_sha256") != source_video_sha256
            or item.get("evidence_ids") != evidence_ids
            or len(expected_timestamps) != len(evidence_ids)
            or item.get("source_timestamps_ms") != expected_timestamps
            or item.get("approved_procedure_revision") != approved_procedure_revision
            or item.get("provider") != provider
            or item.get("generation_stage")
            not in {
                "outline",
                "narration",
                "activities",
                "human_edit",
            }
            or item.get("schema_version") != schema_version
            or (
                schema_version == 2
                and item.get("element_sha256")
                != draft_digest({"path": path, "data": data})
            )
            or item.get("review_status")
            not in {
                "pending_human_review",
                "human_approved",
            }
            or (expected_status and item.get("review_status") != expected_status)
        ):
            raise ValueError("training_provenance_element_mismatch")


def update_training_provenance(
    provenance: dict,
    draft: TrainingDraft,
    provider: dict,
    frames: list[dict],
    source_video_sha256: str,
    approved_procedure_revision: int,
    editor_id: str,
) -> dict:
    previous = {
        item.get("path"): item
        for item in provenance.get("elements", [])
        if isinstance(item, dict)
    }
    frame_by_id = {item["id"]: item["timestamp_ms"] for item in frames}
    elements = []
    for path, text, evidence_ids, generation_stage, data in _training_elements(draft):
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        element_sha256 = draft_digest({"path": path, "data": data})
        old = previous.get(path)
        if old is not None:
            item = dict(old)
            old_element_sha256 = old.get("element_sha256")
            changed = (
                old_element_sha256 != element_sha256
                if old_element_sha256 is not None
                else (
                    old.get("content_sha256") != content_sha256
                    or old.get("evidence_ids") != evidence_ids
                    or path not in {"title", "audience"}
                )
            )
            if changed:
                if old.get("content_sha256") != content_sha256:
                    item["parent_content_sha256"] = old.get("content_sha256")
                if old_element_sha256 is not None:
                    item["parent_element_sha256"] = old_element_sha256
                item["generation_stage"] = "human_edit"
                item["authorship"] = "human"
                item["edited_by"] = editor_id
            item["content_sha256"] = content_sha256
            item["element_sha256"] = element_sha256
            item["evidence_ids"] = evidence_ids
            item["source_timestamps_ms"] = [
                {"evidence_id": ref, "timestamp_ms": frame_by_id[ref]}
                for ref in evidence_ids
            ]
            item["source_video_sha256"] = source_video_sha256
            item["approved_procedure_revision"] = approved_procedure_revision
            item["schema_version"] = 2
            item["review_status"] = "pending_human_review"
        else:
            item = {
                "path": path,
                "content_sha256": content_sha256,
                "element_sha256": element_sha256,
                "source_video_sha256": source_video_sha256,
                "evidence_ids": evidence_ids,
                "source_timestamps_ms": [
                    {"evidence_id": ref, "timestamp_ms": frame_by_id[ref]}
                    for ref in evidence_ids
                ],
                "approved_procedure_revision": approved_procedure_revision,
                "provider": provider,
                "generation_stage": "human_edit",
                "schema_version": 2,
                "review_status": "pending_human_review",
                "authorship": "human",
                "edited_by": editor_id,
            }
        elements.append(item)
    result = {
        **provenance,
        "schema_version": 2,
        "review_status": "pending_human_review",
        "elements": elements,
    }
    result.pop("human_approved_by", None)
    result.pop("human_approved_at", None)
    return result


def validate_script_evidence(script: list[dict], approved_steps: list[dict]) -> None:
    for segment in script:
        references = segment["evidence"]
        if not references:
            if not segment["uncertainty"].startswith("Supplemental guidance"):
                raise ValueError("supplemental_guidance_must_be_labeled")
        elif any(
            not any(
                step["decision"] == "accepted"
                and ref in step["evidence"]
                and step["start_ms"]
                <= segment["start_ms"]
                < segment["end_ms"]
                <= step["end_ms"]
                for step in approved_steps
            )
            for ref in references
        ):
            raise ValueError("script_evidence_outside_approved_interval")


def draft_script(draft: TrainingDraft) -> list[dict]:
    return [
        {
            "id": item.id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "text": item.text,
            "evidence": item.evidence,
            "uncertainty": (
                "Supplemental guidance; not established by recording"
                if item.kind == "supplemental"
                else "Model-generated claim; verify against approved evidence"
            ),
        }
        for item in draft.script
    ]


def draft_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def draft_matches_script(draft: TrainingDraft, script: list[dict]) -> bool:
    fields = ("id", "start_ms", "end_ms", "text", "evidence")
    expected = draft_script(draft)
    return len(expected) == len(script) and all(
        all(left[field] == right[field] for field in fields)
        for left, right in zip(expected, script)
    )


def training_markdown(
    draft: TrainingDraft, sections: tuple[str, ...] | None = None
) -> bytes:
    def safe(text: str) -> str:
        return re.sub(
            r"([\\`*_{}\[\]()#+.!|>-])",
            r"\\\1",
            html.escape(" ".join(text.split()), quote=True),
        )

    def claim(item: TrainingClaim) -> str:
        source = ", ".join(item.evidence) if item.evidence else "supplemental guidance"
        return f"- {safe(item.text)} ({safe(source)})"

    lines = [f"# {safe(draft.title)}", "", f"Audience: {safe(draft.audience)}", ""]
    available = {
        "Prerequisites": draft.prerequisites,
        "Learning objectives": draft.learning_objectives,
        "Chapters": draft.chapters,
        "Teaching script": draft.script,
        "Annotations": draft.annotations,
        "Technical explanations": draft.technical_explanations,
        "Lab steps": draft.lab_steps,
        "Validation checkpoints": draft.validation_checkpoints,
        "Troubleshooting": draft.troubleshooting,
        "Assessment": draft.assessment,
        "Answer explanations": draft.answer_explanations,
    }
    for label in sections if sections is not None else available:
        items = available[label]
        lines += [f"## {label}", ""]
        if not items:
            lines.append("Not provided.")
        for item in items:
            timestamp = (
                f" [{item.start_ms}-{item.end_ms} ms]"
                if isinstance(item, TeachingSegment)
                else ""
            )
            lines.append(claim(item) + timestamp)
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def validate_training_draft(
    value: object, evidence_ids: set[str], duration_ms: int, approved_steps: list[dict]
) -> TrainingDraft:
    draft = TrainingDraft.model_validate(value)
    claims = (
        draft.prerequisites
        + draft.learning_objectives
        + draft.chapters
        + draft.script
        + draft.annotations
        + draft.technical_explanations
        + draft.lab_steps
        + draft.validation_checkpoints
        + draft.troubleshooting
        + draft.assessment
        + draft.answer_explanations
    )
    if any(not set(item.evidence).issubset(evidence_ids) for item in claims):
        raise ValueError("unknown_training_evidence")
    for item in draft.chapters + draft.script + draft.annotations:
        if not item.start_ms < item.end_ms <= duration_ms:
            raise ValueError("invalid_training_timing")
        if item.kind == "evidence" and any(
            not any(
                ref in step["evidence"]
                and step["start_ms"] <= item.start_ms < item.end_ms <= step["end_ms"]
                for step in approved_steps
            )
            for ref in item.evidence
        ):
            raise ValueError("training_evidence_outside_approved_interval")
    return draft


def validate_procedure(
    steps: list[dict],
    evidence_ids: set[str],
    duration_ms: int,
    *,
    approved: bool = False,
) -> list[dict]:
    if not 1 <= len(steps) <= 200:
        raise ValueError("procedure_requires_1_to_200_steps")
    result = []
    ids = set()
    step_types: dict[str, str] = {}
    decision_nodes: dict[str, dict] = {}
    previous_linear_end = 0
    for raw in steps:
        step = ProcedureStep.model_validate(raw)
        if (
            step.id in ids
            or not step.start_ms < step.end_ms <= duration_ms
            or not set(step.evidence).issubset(evidence_ids)
            or any(not c.isprintable() and c not in "\n\t" for c in step.description)
            or (step.step_type == "linear" and step.start_ms < previous_linear_end)
            or (step.decision == "accepted" and not step.description.strip())
            or (step.decision == "accepted" and step.evidence_category == "missing")
            or (step.step_type == "decision" and not step.condition)
            or (step.step_type == "decision" and not step.branch_targets)
            or (step.step_type in {"branch", "join"} and not step.parent_decision)
        ):
            raise ValueError("invalid_or_unreviewed_procedure_step")
        if step.step_type == "decision":
            decision_nodes[step.id] = {
                "condition": step.condition,
                "targets": list(step.branch_targets),
                "default": step.default_target,
            }
            for target in step.branch_targets:
                if target not in ids and target not in {s["id"] for s in steps}:
                    raise ValueError("decision_target_not_found")
        elif step.step_type == "branch":
            if step.parent_decision not in decision_nodes:
                raise ValueError("branch_parent_decision_not_found")
            if step.id not in decision_nodes[step.parent_decision]["targets"]:
                raise ValueError("branch_not_in_parent_targets")
        ids.add(step.id)
        step_types[step.id] = step.step_type
        if step.step_type == "linear":
            previous_linear_end = step.end_ms
        result.append(step.model_dump())
    if approved:
        if any(s["decision"] == "pending" for s in result):
            raise ValueError("invalid_or_unreviewed_procedure_step")
        accepted_ids = {s["id"] for s in result if s["decision"] == "accepted"}
        if not accepted_ids:
            raise ValueError("procedure_requires_accepted_step")
        for step_id in accepted_ids:
            step = next(s for s in result if s["id"] == step_id)
            if step["decision"] == "accepted" and step["confidence"] == "unassessed":
                raise ValueError("invalid_or_unreviewed_procedure_step")
            if step["step_type"] == "decision" and step["decision"] == "accepted":
                if not step["condition"] or not step["condition"].strip():
                    raise ValueError("invalid_or_unreviewed_procedure_step")
            if step["step_type"] in {"decision", "join"}:
                if step["parent_decision"] and step["parent_decision"] not in decision_nodes:
                    raise ValueError("invalid_or_unreviewed_procedure_step")
    return result
