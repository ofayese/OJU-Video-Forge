from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ALLOWED_TOOLS = ("create_render_job",)


class RenderJobArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["render_test"]


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["create_render_job"]
    arguments: RenderJobArguments


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: list[AgentToolCall] = Field(min_length=1, max_length=1)


class AgentAdapter(Protocol):
    def propose(self, task: str, tools: tuple[str, ...]) -> object: ...


class AgentUnavailableError(RuntimeError):
    pass


class DisabledAgentAdapter:
    def propose(self, task: str, tools: tuple[str, ...]) -> object:
        raise AgentUnavailableError("Agent adapter is disabled")


def validate_agent_output(value: object) -> AgentOutput:
    try:
        return AgentOutput.model_validate(value)
    except ValidationError as exc:
        raise ValueError("Malformed agent output") from exc


agent_adapter: AgentAdapter = DisabledAgentAdapter()


def get_agent_adapter() -> AgentAdapter:
    return agent_adapter
