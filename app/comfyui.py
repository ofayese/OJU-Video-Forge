"""Restricted client for explicitly registered ComfyUI workflows.

The adapter deliberately accepts workflow IDs and registered input values rather
than caller-supplied workflow graphs. It is intended for a private ComfyUI
endpoint and does not implement cancellation (ComfyUI's /interrupt is global).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .rendering import RenderError, _run


class ComfyUIError(RuntimeError):
    """Base error for adapter failures."""


class WorkflowNotAllowedError(ComfyUIError):
    """The requested workflow ID or input is not registered."""


class ComfyUIRequestError(ComfyUIError):
    """ComfyUI could not complete an HTTP request."""


class ComfyUIMalformedResponseError(ComfyUIError):
    """ComfyUI returned data that does not match the expected API shape."""


class ComfyUIMissingOutputError(ComfyUIError):
    """The completed workflow did not expose a retrievable output."""


class ComfyUITimeoutError(ComfyUIError):
    """The workflow did not complete before the configured deadline."""


class ComfyUIJobFailedError(ComfyUIError):
    """ComfyUI reported a failed workflow execution."""


class OutputLimitExceededError(ComfyUIError):
    """An output exceeded an adapter byte or file-count limit."""


class MediaValidationError(ComfyUIError):
    """An output could not be validated by FFprobe."""


_WORKFLOW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_PROMPT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MEDIA_SUFFIXES = {
    ".avi",
    ".flac",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".png",
    ".wav",
    ".webm",
    ".webp",
}
_OUTPUT_KEYS = {"audio", "gifs", "images", "videos"}


def _validated_base_url(base_url: str, allowed_hosts: Sequence[str]) -> str:
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise ValueError("ComfyUI base URL must be a short absolute private URL")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ComfyUI base URL must be an HTTP(S) origin without credentials or path")
    try:
        parsed.port  # Validate malformed/out-of-range ports.
    except ValueError as exc:
        raise ValueError("ComfyUI base URL has an invalid port") from exc

    host = parsed.hostname.rstrip(".").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        trusted_names = {"localhost", "comfyui"}
        trusted_names.update(name.rstrip(".").lower() for name in allowed_hosts)
        if host not in trusted_names:
            raise ValueError(
                "ComfyUI hostname must be localhost, the Compose comfyui service, "
                "or an explicitly configured private host"
            )
    else:
        if not (
            (address.is_private or address.is_loopback)
            and not address.is_link_local
            and not address.is_multicast
            and not address.is_reserved
            and not address.is_unspecified
        ):
            raise ValueError("ComfyUI IP address must be loopback or private")

    # Keep the caller's validated port and IPv6 brackets, but normalize away a
    # trailing slash so API paths are always joined from the origin.
    return base_url.rstrip("/")


@dataclass(frozen=True)
class _RegisteredWorkflow:
    workflow_id: str
    graph_json: str
    input_bindings: Mapping[str, tuple[str, str]]
    workflow_sha256: str


class WorkflowRegistry:
    """In-memory registry of trusted, API-format workflow definitions."""

    def __init__(self) -> None:
        self._workflows: dict[str, _RegisteredWorkflow] = {}

    def register(
        self,
        workflow_id: str,
        graph: Mapping[str, Any],
        input_bindings: Mapping[str, tuple[str, str]] | None = None,
    ) -> None:
        if not isinstance(workflow_id, str) or not _WORKFLOW_ID.fullmatch(workflow_id):
            raise ValueError("workflow_id must be a simple registered identifier")
        if workflow_id in self._workflows:
            raise ValueError(f"workflow ID is already registered: {workflow_id}")
        if not isinstance(graph, Mapping) or not graph:
            raise ValueError("workflow graph must be a non-empty API-format node mapping")

        # Round-trip through JSON both validates JSON-serializability and prevents
        # subsequent caller mutations from changing a registered workflow.
        try:
            graph_json = json.dumps(graph, sort_keys=True, separators=(",", ":"), allow_nan=False)
            copied_graph = json.loads(graph_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("workflow graph must contain finite JSON values only") from exc
        if not isinstance(copied_graph, dict):
            raise ValueError("workflow graph must be a node mapping")
        for node_id, node in copied_graph.items():
            if not isinstance(node_id, str) or not node_id or not isinstance(node, dict):
                raise ValueError("workflow graph contains an invalid node")
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                raise ValueError(f"workflow node {node_id} has invalid inputs")

        bindings: dict[str, tuple[str, str]] = {}
        for public_name, target in (input_bindings or {}).items():
            if (
                not isinstance(public_name, str)
                or not public_name
                or not isinstance(target, (tuple, list))
                or len(target) != 2
                or not all(isinstance(part, str) and part for part in target)
            ):
                raise ValueError("input bindings must map names to (node_id, input_name)")
            node_id, input_name = target
            if node_id not in copied_graph or input_name not in copied_graph[node_id].get("inputs", {}):
                raise ValueError(f"input binding {public_name!r} does not match the registered graph")
            bindings[public_name] = (node_id, input_name)

        self._workflows[workflow_id] = _RegisteredWorkflow(
            workflow_id=workflow_id,
            graph_json=graph_json,
            input_bindings=bindings,
            workflow_sha256=hashlib.sha256(graph_json.encode("utf-8")).hexdigest(),
        )

    @property
    def workflow_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._workflows))

    def _lookup(self, workflow_id: str) -> _RegisteredWorkflow:
        try:
            return self._workflows[workflow_id]
        except (KeyError, TypeError) as exc:
            raise WorkflowNotAllowedError(f"workflow ID is not registered: {workflow_id!r}") from exc


@dataclass(frozen=True)
class Submission:
    prompt_id: str
    workflow_id: str
    workflow_sha256: str


@dataclass(frozen=True)
class JobStatus:
    prompt_id: str
    state: str
    detail: str | None = None


@dataclass(frozen=True)
class RetrievedOutput:
    local_path: Path
    source_filename: str
    source_subfolder: str
    size_bytes: int
    sha256: str
    workflow_id: str
    workflow_sha256: str
    prompt_id: str
    comfyui_base_url: str
    retrieved_at: datetime
    media_probe: Mapping[str, Any] | None

    @property
    def ffprobe_validated(self) -> bool:
        return self.media_probe is not None

    def provenance(self) -> dict[str, Any]:
        """Return JSON-compatible source, workflow, media, and checksum metadata."""
        return {
            "workflow_id": self.workflow_id,
            "workflow_sha256": self.workflow_sha256,
            "prompt_id": self.prompt_id,
            "comfyui_base_url": self.comfyui_base_url,
            "source_filename": self.source_filename,
            "source_subfolder": self.source_subfolder,
            "local_filename": self.local_path.name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "retrieved_at": self.retrieved_at.isoformat(),
            "ffprobe_validated": self.ffprobe_validated,
            "media_probe": self.media_probe,
        }


class ComfyUIAdapter:
    """Submit, monitor, and retrieve results from a private ComfyUI instance.

    ``allowed_hosts`` is for an operator-controlled private DNS alias that is
    not the default Compose name. It is an exact hostname allowlist, not a
    wildcard. HTTP requests never follow redirects and ignore proxy environment
    variables. No cancellation method is provided because /interrupt is global.
    """

    def __init__(
        self,
        base_url: str,
        registry: WorkflowRegistry,
        output_dir: str | Path,
        *,
        allowed_hosts: Sequence[str] = (),
        request_timeout: float = 30.0,
        poll_interval: float = 1.0,
        max_wait_seconds: float = 600.0,
        max_workflow_bytes: int = 2 * 1024 * 1024,
        max_output_bytes: int = 512 * 1024 * 1024,
        max_total_output_bytes: int = 2 * 1024 * 1024 * 1024,
        max_output_count: int = 32,
        ffprobe_timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = _validated_base_url(base_url, allowed_hosts)
        if not isinstance(registry, WorkflowRegistry):
            raise TypeError("registry must be a WorkflowRegistry")
        self.registry = registry
        self.output_dir = Path(output_dir).expanduser().resolve()
        self._validate_limit("request_timeout", request_timeout, 0.1, 120.0)
        self._validate_limit("poll_interval", poll_interval, 0.05, 60.0)
        self._validate_limit("max_wait_seconds", max_wait_seconds, 0.1, 3600.0)
        self._validate_limit("ffprobe_timeout", ffprobe_timeout, 0.1, 30.0)
        for name, value, maximum in (
            ("max_workflow_bytes", max_workflow_bytes, 16 *  1024 * 1024),
            ("max_output_bytes", max_output_bytes, 4 * 1024 * 1024 * 1024),
            ("max_total_output_bytes", max_total_output_bytes, 8 * 1024 * 1024 * 1024),
            ("max_output_count", max_output_count, 128),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")
        if max_total_output_bytes < max_output_bytes:
            raise ValueError("max_total_output_bytes must be at least max_output_bytes")
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval
        self.max_wait_seconds = max_wait_seconds
        self.max_workflow_bytes = max_workflow_bytes
        self.max_output_bytes = max_output_bytes
        self.max_total_output_bytes = max_total_output_bytes
        self.max_output_count = max_output_count
        self.ffprobe_timeout = ffprobe_timeout
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(request_timeout),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _validate_limit(name: str, value: float, minimum: float, maximum: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a finite number")
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ComfyUIAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def submit(self, workflow_id: str, inputs: Mapping[str, Any] | None = None) -> Submission:
        registration = self.registry._lookup(workflow_id)
        provided = dict(inputs or {})
        unknown = set(provided) - registration.input_bindings.keys()
        if unknown:
            raise WorkflowNotAllowedError(f"workflow input is not registered: {sorted(map(str, unknown))!r}")

        graph = json.loads(registration.graph_json)
        for public_name, value in provided.items():
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise WorkflowNotAllowedError(f"workflow input {public_name!r} must be a JSON scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise WorkflowNotAllowedError(f"workflow input {public_name!r} must be finite")
            node_id, input_name = registration.input_bindings[public_name]
            graph[node_id]["inputs"][input_name] = value
        try:
            graph_bytes = json.dumps(graph, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError) as exc:
            raise WorkflowNotAllowedError("workflow input could not be encoded as JSON") from exc
        if len(graph_bytes) > self.max_workflow_bytes:
            raise OutputLimitExceededError("registered workflow exceeds the configured request-size limit")

        response_data = self._request_json(
            "POST",
            "/prompt",
            json={"prompt": graph},
            timeout=self.request_timeout,
        )
        prompt_id = response_data.get("prompt_id")
        if not isinstance(prompt_id, str) or not _PROMPT_ID.fullmatch(prompt_id):
            raise ComfyUIMalformedResponseError("ComfyUI submission response has no valid prompt_id")
        return Submission(
            prompt_id=prompt_id,
            workflow_id=workflow_id,
            workflow_sha256=hashlib.sha256(graph_bytes).hexdigest(),
        )

    def get_status(self, prompt_id: str, *, _timeout: float | None = None) -> JobStatus:
        self._validate_prompt_id(prompt_id)
        data = self._request_json(
            "GET",
            f"/history/{quote(prompt_id, safe='')}",
            timeout=_timeout or self.request_timeout,
        )
        if not isinstance(data, dict):
            raise ComfyUIMalformedResponseError("ComfyUI history response must be an object")
        if prompt_id not in data:
            return JobStatus(prompt_id=prompt_id, state="pending")
        record = data[prompt_id]
        if not isinstance(record, dict):
            raise ComfyUIMalformedResponseError("ComfyUI history record must be an object")
        status = record.get("status", {})
        if not isinstance(status, dict):
            raise ComfyUIMalformedResponseError("ComfyUI history status must be an object")
        status_name = status.get("status_str", "")
        if status_name is not None and not isinstance(status_name, str):
            raise ComfyUIMalformedResponseError("ComfyUI status_str must be text")
        detail = self._status_detail(status)
        if (status_name or "").lower() in {"error", "failed", "interrupted"}:
            return JobStatus(prompt_id, "failed", detail)
        if status.get("completed") is True or (status_name or "").lower() in {"success", "succeeded"}:
            return JobStatus(prompt_id, "succeeded", detail)
        return JobStatus(prompt_id, "running", detail)

    def wait_for_completion(self, prompt_id: str, *, timeout: float = 300.0) -> JobStatus:
        self._validate_limit("timeout", timeout, 0.1, self.max_wait_seconds)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ComfyUITimeoutError(f"ComfyUI prompt {prompt_id!r} did not complete before timeout")
            status = self.get_status(prompt_id, _timeout=min(self.request_timeout, remaining))
            if status.state == "succeeded":
                return status
            if status.state == "failed":
                raise ComfyUIJobFailedError(
                    f"ComfyUI prompt {prompt_id!r} failed"
                    + (f": {status.detail}" if status.detail else "")
                )
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(self.poll_interval, remaining))

    def retrieve_outputs(self, submission: Submission) -> list[RetrievedOutput]:
        if not isinstance(submission, Submission):
            raise TypeError("retrieve_outputs requires a Submission returned by this adapter")
        self._validate_prompt_id(submission.prompt_id)
        registration = self.registry._lookup(submission.workflow_id)
        if not re.fullmatch(r"[0-9a-f]{64}", submission.workflow_sha256):
            raise ValueError("submission has an invalid workflow checksum")

        history = self._request_json(
            "GET",
            f"/history/{quote(submission.prompt_id, safe='')}",
            timeout=self.request_timeout,
        )
        if not isinstance(history, dict):
            raise ComfyUIMalformedResponseError("ComfyUI history response must be an object")
        record = history.get(submission.prompt_id)
        if not isinstance(record, dict):
            raise ComfyUIMissingOutputError("ComfyUI has no history record for the submitted prompt")
        status = record.get("status", {})
        if not isinstance(status, dict):
            raise ComfyUIMalformedResponseError("ComfyUI history status must be an object")
        status_name = str(status.get("status_str", "")).lower()
        if status_name in {"error", "failed", "interrupted"}:
            raise ComfyUIJobFailedError(
                f"ComfyUI prompt {submission.prompt_id!r} failed"
                + (f": {self._status_detail(status)}" if self._status_detail(status) else "")
            )
        if status.get("completed") is not True and status_name not in {"success", "succeeded"}:
            raise ComfyUIMissingOutputError("ComfyUI prompt has not completed successfully")

        descriptors = self._output_descriptors(record.get("outputs"))
        if not descriptors:
            raise ComfyUIMissingOutputError("ComfyUI history contains no supported output files")
        if len(descriptors) > self.max_output_count:
            raise OutputLimitExceededError("ComfyUI output file count exceeds the configured limit")

        self.output_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        results: list[RetrievedOutput] = []
        total_bytes = 0
        try:
            for index, descriptor in enumerate(descriptors):
                result, total_bytes = self._retrieve_one(
                    submission,
                    registration,
                    descriptor,
                    index,
                    total_bytes,
                )
                results.append(result)
        except Exception:
            # Do not leave a partial set of apparently complete results behind.
            for result in results:
                result.local_path.unlink(missing_ok=True)
            raise
        return results

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                path,
                json=json,
                timeout=timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyUIRequestError(f"ComfyUI {method} {path} request failed") from exc
        if not isinstance(data, dict):
            raise ComfyUIMalformedResponseError("ComfyUI JSON response must be an object")
        return data

    @staticmethod
    def _status_detail(status: Mapping[str, Any]) -> str | None:
        messages = status.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if isinstance(item, list) and len(item) > 1 and isinstance(item[1], dict):
                    message = item[1].get("exception_message") or item[1].get("message")
                    if isinstance(message, str):
                        return message[:300]
                if isinstance(item, dict):
                    message = item.get("exception_message") or item.get("message")
                    if isinstance(message, str):
                        return message[:300]
        return None

    @staticmethod
    def _validate_prompt_id(prompt_id: str) -> None:
        if not isinstance(prompt_id, str) or not _PROMPT_ID.fullmatch(prompt_id):
            raise ValueError("prompt_id is invalid")

    @staticmethod
    def _output_descriptors(outputs: Any) -> list[dict[str, str]]:
        if not isinstance(outputs, dict):
            raise ComfyUIMalformedResponseError("ComfyUI history outputs must be an object")
        descriptors: list[dict[str, str]] = []
        for node_id in sorted(outputs, key=str):
            node_output = outputs[node_id]
            if not isinstance(node_output, dict):
                raise ComfyUIMalformedResponseError("ComfyUI node output must be an object")
            for output_key in sorted(_OUTPUT_KEYS.intersection(node_output)):
                files = node_output[output_key]
                if not isinstance(files, list):
                    raise ComfyUIMalformedResponseError(f"ComfyUI {output_key} output must be a list")
                for item in files:
                    if not isinstance(item, dict):
                        raise ComfyUIMalformedResponseError("ComfyUI output descriptor must be an object")
                    filename = item.get("filename")
                    subfolder = item.get("subfolder", "")
                    file_type = item.get("type", "output")
                    if (
                        not isinstance(filename, str)
                        or not filename
                        or len(filename) > 255
                        or "/" in filename
                        or "\\" in filename
                        or "\x00" in filename
                        or filename in {".", ".."}
                        or not isinstance(subfolder, str)
                        or len(subfolder) > 512
                        or "\\" in subfolder
                        or "\x00" in subfolder
                        or not isinstance(file_type, str)
                        or file_type != "output"
                    ):
                        raise ComfyUIMalformedResponseError("ComfyUI output descriptor contains unsafe fields")
                    path_parts = subfolder.split("/") if subfolder else []
                    if any(part in {"", ".", ".."} or ":" in part for part in path_parts):
                        raise ComfyUIMalformedResponseError("ComfyUI output subfolder is unsafe")
                    if PureWindowsPath(filename).name != filename:
                        raise ComfyUIMalformedResponseError("ComfyUI output filename is unsafe")
                    if Path(filename).suffix.lower() not in _MEDIA_SUFFIXES:
                        raise ComfyUIMalformedResponseError("ComfyUI output has an unsupported media extension")
                    descriptors.append(
                        {"filename": filename, "subfolder": subfolder, "type": file_type}
                    )
        return descriptors

    def _retrieve_one(
        self,
        submission: Submission,
        registration: _RegisteredWorkflow,
        descriptor: Mapping[str, str],
        index: int,
        bytes_already_retrieved: int,
    ) -> tuple[RetrievedOutput, int]:
        filename = descriptor["filename"]
        suffix = Path(filename).suffix.lower()
        destination = self.output_dir / (
            f"{submission.prompt_id}_{index:03d}_{uuid.uuid4().hex}{suffix}"
        )
        temporary_path: Path | None = None
        digest = hashlib.sha256()
        size = 0
        try:
            with self._client.stream(
                "GET",
                "/view",
                params={
                    "filename": filename,
                    "subfolder": descriptor["subfolder"],
                    "type": descriptor["type"],
                },
                timeout=self.request_timeout,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        announced_size = int(content_length)
                    except ValueError as exc:
                        raise ComfyUIMalformedResponseError("ComfyUI returned an invalid Content-Length") from exc
                    if announced_size < 0:
                        raise ComfyUIMalformedResponseError("ComfyUI returned an invalid Content-Length")
                    if announced_size > self.max_output_bytes:
                        raise OutputLimitExceededError("ComfyUI output exceeds the per-file byte limit")
                    if bytes_already_retrieved + announced_size > self.max_total_output_bytes:
                        raise OutputLimitExceededError("ComfyUI outputs exceed the total byte limit")

                file_descriptor, temp_name = tempfile.mkstemp(prefix=".comfyui-", suffix=".part", dir=self.output_dir)
                temporary_path = Path(temp_name)
                with os.fdopen(file_descriptor, "wb") as target:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_output_bytes:
                            raise OutputLimitExceededError("ComfyUI output exceeds the per-file byte limit")
                        if bytes_already_retrieved + size > self.max_total_output_bytes:
                            raise OutputLimitExceededError("ComfyUI outputs exceed the total byte limit")
                        digest.update(chunk)
                        target.write(chunk)
            if size == 0:
                raise ComfyUIMalformedResponseError("ComfyUI returned an empty output file")
            # A UUID prevents accidental replacement of another retrieved output;
            # os.replace keeps the final path from exposing a partial download.
            os.replace(temporary_path, destination)
            temporary_path = None
            media_probe = self._probe_media(destination)
            return (
                RetrievedOutput(
                    local_path=destination,
                    source_filename=filename,
                    source_subfolder=descriptor["subfolder"],
                    size_bytes=size,
                    sha256=digest.hexdigest(),
                    workflow_id=submission.workflow_id,
                    workflow_sha256=submission.workflow_sha256,
                    prompt_id=submission.prompt_id,
                    comfyui_base_url=self.base_url,
                    retrieved_at=datetime.now(UTC),
                    media_probe=media_probe,
                ),
                bytes_already_retrieved + size,
            )
        except httpx.HTTPError as exc:
            raise ComfyUIRequestError(f"ComfyUI output retrieval failed for {filename!r}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _probe_media(self, path: Path) -> Mapping[str, Any] | None:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            return None
        command = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ]
        try:
            result = _run(command, path.parent, self.ffprobe_timeout)
        except RenderError as exc:
            path.unlink(missing_ok=True)
            raise MediaValidationError("FFprobe could not validate the retrieved media") from exc
        try:
            metadata = json.loads(result)
        except (TypeError, ValueError) as exc:
            path.unlink(missing_ok=True)
            raise MediaValidationError("FFprobe returned malformed metadata") from exc
        if not isinstance(metadata, dict) or not isinstance(metadata.get("streams"), list) or not metadata["streams"]:
            path.unlink(missing_ok=True)
            raise MediaValidationError("FFprobe found no media streams")
        return metadata
