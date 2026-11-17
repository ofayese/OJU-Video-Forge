"""ERE Windows host telemetry collector and authenticated publisher.

Collects available physical memory and NVIDIA GPU VRAM from the Windows host
via PowerShell, signs each observation with HMAC-SHA256, and publishes it to
a telemetry file consumable by the OJU inference coordinator.

The publisher never embeds the signing key in the telemetry file or in any
read-only data mount; the key is supplied separately at publish time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COLLECTOR_VERSION = "1.0.0"
COLLECTOR_SCRIPT_NAME = "collect-host-telemetry.ps1"
DEFAULT_MAX_AGE_SECONDS = 30
MAX_TELEMETRY_FILE_BYTES = 64 * 1024
SEQUENCE_STATE_FILENAME = ".telemetry-sequence-state.json"
DEFAULT_CLOCK_SKEW_SECONDS = 5


class HostTelemetryError(RuntimeError):
    """Raised when host telemetry collection or publishing cannot proceed."""


class CollectorUnavailable(HostTelemetryError):
    """Raised when the host collector cannot be invoked or produces no output."""


class MeasurementInvalid(HostTelemetryError):
    """Raised when a collected measurement is unavailable, malformed, or contradictory."""


@dataclass(frozen=True)
class TelemetryObservation:
    collected_at: float
    max_age_seconds: int
    source_id: str
    available_ram_bytes: int
    gpu_capacity_ok: bool
    gpu_free_bytes: int | None
    gpu_total_bytes: int | None
    measurement_status: str
    collector_version: str
    host_id: str
    sequence: int
    iso_timestamp: str

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "collected_at": self.collected_at,
            "max_age_seconds": self.max_age_seconds,
            "source_id": self.source_id,
            "available_ram_bytes": self.available_ram_bytes,
            "gpu_capacity_ok": self.gpu_capacity_ok,
            "measurement_status": self.measurement_status,
            "collector_version": self.collector_version,
            "host_id": self.host_id,
            "sequence": self.sequence,
            "iso_timestamp": self.iso_timestamp,
        }
        if self.gpu_free_bytes is not None:
            payload["gpu_free_bytes"] = self.gpu_free_bytes
        if self.gpu_total_bytes is not None:
            payload["gpu_total_bytes"] = self.gpu_total_bytes
        return payload


def canonical_payload(payload: dict[str, Any]) -> bytes:
    """Canonical JSON byte encoding matching the coordinator signature scheme."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_payload(payload: dict[str, Any], key: bytes) -> str:
    """HMAC-SHA256 signature over the canonical payload encoding."""
    return hmac.new(key, canonical_payload(payload), hashlib.sha256).hexdigest()


def build_envelope(payload: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Build a signature envelope compatible with read_trusted_telemetry()."""
    return {"signature": sign_payload(payload, key), "payload": payload}


def _resolve_powershell() -> str:
    """Locate an available PowerShell interpreter (pwsh preferred, powershell fallback)."""
    for name in ("pwsh", "powershell"):
        try:
            result = subprocess.run(
                ["where", name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            continue
        if result.returncode == 0 and result.stdout.strip():
            return name
    raise CollectorUnavailable("no PowerShell interpreter found on PATH")


def _collector_script_path() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / COLLECTOR_SCRIPT_NAME


def _run_collector(
    sequence: int,
    source_id: str,
    collector_version: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Invoke the PowerShell collector and return the parsed measurement JSON."""
    script = _collector_script_path()
    if not script.is_file():
        raise CollectorUnavailable(f"collector script not found: {script}")
    powershell = _resolve_powershell()
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Sequence",
                str(int(sequence)),
                "-SourceId",
                str(source_id),
                "-CollectorVersion",
                str(collector_version),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CollectorUnavailable("PowerShell interpreter disappeared during invocation") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorUnavailable(f"collector timed out after {timeout_seconds}s") from exc

    raw = (completed.stdout or "").strip()
    if not raw:
        stderr = (completed.stderr or "").strip()
        raise CollectorUnavailable(
            f"collector produced no output (exit {completed.returncode}): {stderr}"
        )
    try:
        measurement = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectorUnavailable(f"collector output is not valid JSON: {exc}") from exc
    if not isinstance(measurement, dict):
        raise CollectorUnavailable("collector output is not a JSON object")
    return measurement


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MeasurementInvalid(f"{field} must be an integer")
    return value


def _require_non_negative_int(value: Any, field: str) -> int:
    result = _require_int(value, field)
    if result < 0:
        raise MeasurementInvalid(f"{field} must not be negative")
    return result


def validate_measurement(measurement: dict[str, Any]) -> TelemetryObservation:
    """Validate a raw collector measurement and build a TelemetryObservation.

    Rejects unavailable, malformed, or contradictory readings. Never fabricates
    GPU capacity: absent GPU measurements are reported as null with
    gpu_capacity_ok=false.
    """
    if not isinstance(measurement, dict):
        raise MeasurementInvalid("measurement is not an object")

    status = measurement.get("measurement_status")
    if not isinstance(status, str):
        raise MeasurementInvalid("measurement_status is missing or not a string")

    source_id = measurement.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        raise MeasurementInvalid("source_id is missing or empty")

    host_id = measurement.get("host_id")
    if not isinstance(host_id, str) or not host_id:
        raise MeasurementInvalid("host_id is missing or empty")

    collector_version = measurement.get("collector_version")
    if not isinstance(collector_version, str) or not collector_version:
        raise MeasurementInvalid("collector_version is missing or empty")

    sequence = measurement.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise MeasurementInvalid("sequence is missing or not a non-negative integer")

    iso_timestamp = measurement.get("iso_timestamp")
    if not isinstance(iso_timestamp, str) or not iso_timestamp:
        raise MeasurementInvalid("iso_timestamp is missing or empty")

    collected_at_raw = measurement.get("collected_at")
    try:
        collected_at = float(collected_at_raw)
    except (TypeError, ValueError) as exc:
        raise MeasurementInvalid("collected_at is missing or not a number") from exc
    if math.isnan(collected_at) or collected_at in (float("inf"), float("-inf")):
        raise MeasurementInvalid("collected_at is not a finite number")

    max_age_raw = measurement.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS)
    try:
        max_age_seconds = int(max_age_raw)
    except (TypeError, ValueError) as exc:
        raise MeasurementInvalid("max_age_seconds is not an integer") from exc
    if max_age_seconds <= 0:
        raise MeasurementInvalid("max_age_seconds must be positive")

    available_ram = _require_non_negative_int(measurement.get("available_ram_bytes"), "available_ram_bytes")
    if available_ram == 0:
        raise MeasurementInvalid("available_ram_bytes is zero; cannot confirm usable RAM")

    gpu_capacity_ok = measurement.get("gpu_capacity_ok")
    if not isinstance(gpu_capacity_ok, bool):
        raise MeasurementInvalid("gpu_capacity_ok must be a boolean")

    gpu_free = measurement.get("gpu_free_bytes")
    gpu_total = measurement.get("gpu_total_bytes")
    gpu_free_int: int | None = None
    gpu_total_int: int | None = None
    if gpu_free is not None:
        gpu_free_int = _require_non_negative_int(gpu_free, "gpu_free_bytes")
    if gpu_total is not None:
        gpu_total_int = _require_non_negative_int(gpu_total, "gpu_total_bytes")
    if gpu_free_int is not None and gpu_total_int is not None and gpu_free_int > gpu_total_int:
        raise MeasurementInvalid("gpu_free_bytes exceeds gpu_total_bytes")
    if gpu_capacity_ok and gpu_total_int is None:
        raise MeasurementInvalid("gpu_capacity_ok is true but gpu_total_bytes is not measured")

    return TelemetryObservation(
        collected_at=collected_at,
        max_age_seconds=max_age_seconds,
        source_id=source_id,
        available_ram_bytes=available_ram,
        gpu_capacity_ok=gpu_capacity_ok,
        gpu_free_bytes=gpu_free_int,
        gpu_total_bytes=gpu_total_int,
        measurement_status=status,
        collector_version=collector_version,
        host_id=host_id,
        sequence=int(sequence),
        iso_timestamp=iso_timestamp,
    )


def collect_telemetry(
    sequence: int,
    source_id: str,
    collector_version: str = COLLECTOR_VERSION,
    timeout_seconds: float = 30.0,
) -> TelemetryObservation:
    """Collect a fresh host telemetry observation from the Windows host."""
    measurement = _run_collector(
        sequence=sequence,
        source_id=source_id,
        collector_version=collector_version,
        timeout_seconds=timeout_seconds,
    )
    return validate_measurement(measurement)


class SequenceStore:
    """Durable, atomic per-process sequence counter surviving collector restarts.

    Stored as a small JSON file next to the telemetry output. The collector reads
    the last emitted sequence and increments it, so a restarted collector never
    reuses a sequence the consumer may already have accepted.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> int:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0
        except OSError as exc:
            raise HostTelemetryError(f"unable to read sequence state {self.path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HostTelemetryError(f"sequence state {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise HostTelemetryError(f"sequence state {self.path} is not an object")
        value = data.get("last_sequence", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HostTelemetryError(f"sequence state {self.path} has invalid last_sequence")
        return value

    def next(self) -> int:
        """Atomically read the last sequence, increment, persist, and return it.

        The in-process lock serializes concurrent callers; cross-process
        concurrent collection is not supported (single writer by design).
        """
        with self._lock:
            last = self.read()
            next_value = last + 1
            self.write(next_value)
            return next_value

    def write(self, value: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"last_sequence": int(value)}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def _default_sequence_state(output_path: Path | str) -> Path:
    return Path(output_path).with_name(SEQUENCE_STATE_FILENAME)


def apply_file_protection(path: Path | str) -> None:
    """Apply least-privilege ACLs to the telemetry file and its folder.

    On Windows this restricts access to the local Administrators group and the
    owning account, disabling inheritance. On other platforms it falls back to
    owner-only mode bits. Failures are logged, not raised, so a publisher running
    without elevation can still emit the file (operators must harden ACLs
    separately where required).
    """
    target = Path(path)
    folder = target.parent if target.parent.exists() else target
    owner = os.environ.get("USERNAME") or os.environ.get("USER") or "owner"
    # Invoke icacls.exe directly (not via powershell -Command) so that the
    # permission shorthand such as :(R) and :(F) is not mangled by PowerShell
    # history expansion. Each command is run as a list so no shell parsing occurs.
    commands = [
        ["icacls", str(folder), "/inheritance:r"],
        ["icacls", str(folder), "/grant", f"{owner}:(F)"],
        ["icacls", str(folder), "/grant", "Administrators:(F)"],
        ["icacls", str(target), "/inheritance:r"],
        ["icacls", str(target), "/grant", f"{owner}:(R)"],
        ["icacls", str(target), "/grant", "Administrators:(F)"],
    ]
    for command in commands:
        try:
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("file protection command skipped: %s (%s)", command, exc)
    try:
        os.chmod(target, 0o600)
    except OSError as exc:
        logger.debug("could not set mode bits on %s: %s", target, exc)


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Atomically replace path with data via temp file + fsync + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def publish_telemetry(
    observation: TelemetryObservation,
    key: bytes,
    output_path: Path | str,
    sequence_store: SequenceStore | None = None,
    max_file_bytes: int = MAX_TELEMETRY_FILE_BYTES,
) -> dict[str, Any]:
    """Sign and atomically publish an observation to the telemetry file.

    Returns the published envelope. The signing key is never written to the file.
    """
    payload = observation.to_payload()
    envelope = build_envelope(payload, key)
    serialized = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(serialized) > max_file_bytes:
        raise HostTelemetryError(
            f"serialized telemetry exceeds max_file_bytes ({len(serialized)} > {max_file_bytes})"
        )

    target = Path(output_path)
    store = sequence_store or SequenceStore(_default_sequence_state(target))
    sequence_store_used = store
    # The observation.sequence is authoritative; store is only advanced on behalf
    # of callers that did not pre-assign a sequence. If no store is provided we
    # still persist the observed sequence for continuity.
    if sequence_store_used is None:
        sequence_store_used = SequenceStore(_default_sequence_state(target))
    try:
        sequence_store_used.write(observation.sequence)
    except HostTelemetryError:
        logger.warning("could not persist sequence state for %s", target)

    atomic_write_bytes(target, serialized)
    apply_file_protection(target)
    return envelope


def load_envelope(path: Path | str, max_file_bytes: int = MAX_TELEMETRY_FILE_BYTES) -> dict[str, Any]:
    """Load and strictly parse a telemetry envelope file (max size, strict JSON)."""
    target = Path(path)
    try:
        data = target.read_bytes()
    except FileNotFoundError as exc:
        raise HostTelemetryError(f"telemetry file not found: {target}") from exc
    except OSError as exc:
        raise HostTelemetryError(f"unable to read telemetry file {target}: {exc}") from exc
    if len(data) == 0:
        raise HostTelemetryError(f"telemetry file is empty: {target}")
    if len(data) > max_file_bytes:
        raise HostTelemetryError(
            f"telemetry file exceeds max_file_bytes ({len(data)} > {max_file_bytes})"
        )
    try:
        envelope = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HostTelemetryError(f"telemetry file is not valid JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise HostTelemetryError("telemetry envelope is not a JSON object")
    return envelope


def current_timestamp() -> float:
    return time.time()
