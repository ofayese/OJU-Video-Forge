"""Evidence review primitives for silent-recording training workflows."""

import hashlib
import html
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
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


class TrainingClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["evidence", "supplemental"]
    evidence: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def evidence_required(self):
        if (not self.text.strip()
                or any(not char.isprintable() and char not in "\n\t" for char in self.text)
                or (self.kind == "evidence" and not self.evidence)):
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
    prerequisites: list[TrainingClaim] = Field(max_length=20)
    learning_objectives: list[TrainingClaim] = Field(min_length=1, max_length=20)
    chapters: list[TeachingSegment] = Field(min_length=1, max_length=100)
    script: list[TeachingSegment] = Field(min_length=1, max_length=200)
    annotations: list[TeachingSegment] = Field(max_length=200)
    technical_explanations: list[TrainingClaim] = Field(max_length=100)
    lab_steps: list[TrainingClaim] = Field(max_length=100)
    validation_checkpoints: list[TrainingClaim] = Field(max_length=100)
    troubleshooting: list[TrainingClaim] = Field(max_length=100)
    assessment: list[TrainingClaim] = Field(max_length=100)
    answer_explanations: list[TrainingClaim] = Field(max_length=100)


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
                and step["start_ms"] <= segment["start_ms"] < segment["end_ms"] <= step["end_ms"]
                for step in approved_steps
            )
            for ref in references
        ):
            raise ValueError("script_evidence_outside_approved_interval")


def draft_script(draft: TrainingDraft) -> list[dict]:
    return [{
        "id": item.id, "start_ms": item.start_ms, "end_ms": item.end_ms,
        "text": item.text, "evidence": item.evidence,
        "uncertainty": (
            "Supplemental guidance; not established by recording"
            if item.kind == "supplemental"
            else "Model-generated claim; verify against approved evidence"
        ),
    } for item in draft.script]


def draft_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def draft_matches_script(draft: TrainingDraft, script: list[dict]) -> bool:
    fields = ("id", "start_ms", "end_ms", "text", "evidence")
    expected = draft_script(draft)
    return len(expected) == len(script) and all(
        all(left[field] == right[field] for field in fields)
        for left, right in zip(expected, script)
    )


def training_markdown(draft: TrainingDraft, sections: tuple[str, ...] | None = None) -> bytes:
    def safe(text: str) -> str:
        return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(
            " ".join(text.split()), quote=True
        ))

    def claim(item: TrainingClaim) -> str:
        source = ", ".join(item.evidence) if item.evidence else "supplemental guidance"
        return f"- {safe(item.text)} ({safe(source)})"

    lines = [f"# {safe(draft.title)}", "", f"Audience: {safe(draft.audience)}", ""]
    available = {
        "Prerequisites": draft.prerequisites, "Learning objectives": draft.learning_objectives,
        "Chapters": draft.chapters, "Teaching script": draft.script, "Annotations": draft.annotations,
        "Technical explanations": draft.technical_explanations, "Lab steps": draft.lab_steps,
        "Validation checkpoints": draft.validation_checkpoints, "Troubleshooting": draft.troubleshooting,
        "Assessment": draft.assessment, "Answer explanations": draft.answer_explanations,
    }
    for label in sections if sections is not None else available:
        items = available[label]
        lines += [f"## {label}", ""]
        if not items:
            lines.append("Not provided.")
        for item in items:
            timestamp = f" [{item.start_ms}-{item.end_ms} ms]" if isinstance(item, TeachingSegment) else ""
            lines.append(claim(item) + timestamp)
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def validate_training_draft(
    value: object, evidence_ids: set[str], duration_ms: int, approved_steps: list[dict]
) -> TrainingDraft:
    draft = TrainingDraft.model_validate(value)
    claims = (
        draft.prerequisites + draft.learning_objectives + draft.chapters + draft.script
        + draft.annotations + draft.technical_explanations + draft.lab_steps
        + draft.validation_checkpoints + draft.troubleshooting + draft.assessment
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
    steps: list[dict], evidence_ids: set[str], duration_ms: int, *, approved: bool = False
) -> list[dict]:
    if not 1 <= len(steps) <= 200:
        raise ValueError("procedure_requires_1_to_200_steps")
    result = []
    ids = set()
    previous_end = 0
    for raw in steps:
        step = ProcedureStep.model_validate(raw)
        if (
            step.id in ids
            or step.start_ms < previous_end
            or not step.start_ms < step.end_ms <= duration_ms
            or not set(step.evidence).issubset(evidence_ids)
            or any(not c.isprintable() and c not in "\n\t" for c in step.description)
            or (step.decision == "accepted" and not step.description.strip())
            or (step.decision == "accepted" and step.evidence_category == "missing")
            or (approved and step.decision == "accepted" and step.confidence == "unassessed")
            or (approved and step.decision == "pending")
        ):
            raise ValueError("invalid_or_unreviewed_procedure_step")
        ids.add(step.id)
        previous_end = step.end_ms
        result.append(step.model_dump())
    if approved and not any(step["decision"] == "accepted" for step in result):
        raise ValueError("procedure_requires_accepted_step")
    return result
