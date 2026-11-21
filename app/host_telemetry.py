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
import re
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

# Local machine trustees by well-known SID. icacls accepts a permission *symbol*
# such as ``(F)`` but rejects a raw SDDL rights string such as ``FA`` (exit code 87),
# and account names are localised, so a SID is mapped back to its symbol before it is
# re-granted. Only non-shared principals are listed: SYSTEM, the local
# Administrators group and the owning account. Everyone / Users / Authenticated
# Users are intentionally absent so folder hardening can never re-grant them.
_LOCAL_TRUSTEE_SYMBOLS = {
    "S-1-5-18": "SYSTEM",
    "S-1-5-32-544": "Administrators",
}


class HostTelemetryError(RuntimeError):
    """Raised when host telemetry collection or publishing cannot proceed."""


class CollectorUnavailableError(HostTelemetryError):
    """Raised when the host collector cannot be invoked or produces no output."""


CollectorUnavailable = CollectorUnavailableError


class MeasurementInvalidError(HostTelemetryError):
    """Raised when a collected measurement is unavailable, malformed, or contradictory."""


MeasurementInvalid = MeasurementInvalidError


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
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            continue
        if result.returncode == 0 and result.stdout.strip():
            return name
    raise CollectorUnavailableError("no PowerShell interpreter found on PATH")


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
        raise CollectorUnavailableError(f"collector script not found: {script}")
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
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CollectorUnavailableError("PowerShell interpreter disappeared during invocation") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorUnavailableError(f"collector timed out after {timeout_seconds}s") from exc

    raw = (completed.stdout or "").strip()
    if not raw:
        stderr = (completed.stderr or "").strip()
        raise CollectorUnavailableError(
            f"collector produced no output (exit {completed.returncode}): {stderr}"
        )
    try:
        measurement = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectorUnavailableError(f"collector output is not valid JSON: {exc}") from exc
    if not isinstance(measurement, dict):
        raise CollectorUnavailableError("collector output is not a JSON object")
    return measurement


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MeasurementInvalidError(f"{field} must be an integer")
    return value


def _require_non_negative_int(value: Any, field: str) -> int:
    result = _require_int(value, field)
    if result < 0:
        raise MeasurementInvalidError(f"{field} must not be negative")
    return result


def validate_measurement(measurement: dict[str, Any]) -> TelemetryObservation:
    """Validate a raw collector measurement and build a TelemetryObservation.

    Rejects unavailable, malformed, or contradictory readings. Never fabricates
    GPU capacity: absent GPU measurements are reported as null with
    gpu_capacity_ok=false.
    """
    if not isinstance(measurement, dict):
        raise MeasurementInvalidError("measurement is not an object")

    status = measurement.get("measurement_status")
    if not isinstance(status, str):
        raise MeasurementInvalidError("measurement_status is missing or not a string")

    source_id = measurement.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        raise MeasurementInvalidError("source_id is missing or empty")

    host_id = measurement.get("host_id")
    if not isinstance(host_id, str) or not host_id:
        raise MeasurementInvalidError("host_id is missing or empty")

    collector_version = measurement.get("collector_version")
    if not isinstance(collector_version, str) or not collector_version:
        raise MeasurementInvalidError("collector_version is missing or empty")

    sequence = measurement.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise MeasurementInvalidError("sequence is missing or not a non-negative integer")

    iso_timestamp = measurement.get("iso_timestamp")
    if not isinstance(iso_timestamp, str) or not iso_timestamp:
        raise MeasurementInvalidError("iso_timestamp is missing or empty")

    collected_at_raw = measurement.get("collected_at")
    try:
        collected_at = float(collected_at_raw)
    except (TypeError, ValueError) as exc:
        raise MeasurementInvalidError("collected_at is missing or not a number") from exc
    if math.isnan(collected_at) or collected_at in (float("inf"), float("-inf")):
        raise MeasurementInvalidError("collected_at is not a finite number")

    max_age_raw = measurement.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS)
    try:
        max_age_seconds = int(max_age_raw)
    except (TypeError, ValueError) as exc:
        raise MeasurementInvalidError("max_age_seconds is not an integer") from exc
    if max_age_seconds <= 0:
        raise MeasurementInvalidError("max_age_seconds must be positive")

    available_ram = _require_non_negative_int(measurement.get("available_ram_bytes"), "available_ram_bytes")
    if available_ram == 0:
        raise MeasurementInvalidError("available_ram_bytes is zero; cannot confirm usable RAM")

    gpu_capacity_ok = measurement.get("gpu_capacity_ok")
    if not isinstance(gpu_capacity_ok, bool):
        raise MeasurementInvalidError("gpu_capacity_ok must be a boolean")

    gpu_free = measurement.get("gpu_free_bytes")
    gpu_total = measurement.get("gpu_total_bytes")
    gpu_free_int: int | None = None
    gpu_total_int: int | None = None
    if gpu_free is not None:
        gpu_free_int = _require_non_negative_int(gpu_free, "gpu_free_bytes")
    if gpu_total is not None:
        gpu_total_int = _require_non_negative_int(gpu_total, "gpu_total_bytes")
    if gpu_free_int is not None and gpu_total_int is not None and gpu_free_int > gpu_total_int:
        raise MeasurementInvalidError("gpu_free_bytes exceeds gpu_total_bytes")
    if gpu_capacity_ok and gpu_total_int is None:
        raise MeasurementInvalidError("gpu_capacity_ok is true but gpu_total_bytes is not measured")

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


def _is_canonical_sequence_state(path: Path) -> bool:
    """True only if ``path`` carries the canonical sequence-state payload.

    A telemetry directory may also hold operator-authored files that end in
    ``.json`` (for example a legacy ``sequence.json`` written by an older
    publisher). Those files were never created by ``SequenceStore`` and must not be
    granted the observation's least-privilege ACL, because the publisher does not own
    their replacement: revoking inheritance on them can lock the operator out.
    Only ``{"last_sequence": <int>}`` is recognised; any read or parse failure is
    treated as "not canonical" so the file is simply left untouched (fail closed).
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return False
    return (
        isinstance(data, dict)
        and set(data) == {"last_sequence"}
        and isinstance(data["last_sequence"], int)
        and not isinstance(data["last_sequence"], bool)
    )


def _is_telemetry_directory(path: Path) -> bool:
    """True only for a directory dedicated to telemetry output.

    Revoking ACL inheritance on a shared directory (temp roots, user profiles,
    project roots) breaks the owner's own access to unrelated files, so folder
    hardening is applied only to a directory that exists solely to hold telemetry.

    Enumeration failure is treated as "not a telemetry directory" (fail closed):
    an inaccessible path must never be granted hardening that assumes ownership.
    """
    try:
        entries = list(path.iterdir())
    except OSError as exc:
        logger.debug("telemetry directory probe denied for %s (%s)", path, type(exc).__name__)
        return False
    return bool(entries) and all(
        entry.is_file() and (entry.name.startswith("telemetry") or entry.name.endswith(".json"))
        for entry in entries
    )


def _acl_entries(path: Path) -> list[tuple[str, str]]:
    """Return ``(sid, rights)`` for every ACE currently on ``path``.

    icacls prints the ACL in SDDL when asked to ``/save`` it, and SDDL always names
    trustees by SID - so this is independent of the UI language and never guesses
    account names. Unparseable output yields an empty list so the caller falls back
    to leaving the path unhardened rather than hardening blindly.
    """
    handle, temp_name = tempfile.mkstemp(prefix="aclsave", suffix=".txt")
    os.close(handle)
    try:
        result = subprocess.run(
            ["icacls", str(path), "/save", temp_name, "/L"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        # icacls writes the /save listing as UTF-16LE regardless of the console code
        # page; decoding it as utf-8 yields interleaved NULs and no ACEs at all.
        raw = Path(temp_name).read_bytes()
        try:
            text = raw.decode("utf-16")
        except UnicodeError:
            text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return []
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
    if len(lines) < 2:
        return []
    sddl = lines[1].strip()
    if sddl.startswith("D:"):
        sddl = sddl[2:]
    entries: list[tuple[str, str]] = []
    for ace in re.findall(r"\(([^()]*)\)", sddl):
        fields = ace.split(";")
        if len(fields) < 6 or fields[0].startswith("D"):
            continue
        sid = fields[5]
        rights = fields[2]
        if sid.startswith("S-") and rights:
            entries.append((sid, rights))
    return entries


def _protect_windows_directory(folder: Path, owner: str) -> bool:
    """Least-privilege ACL on a dedicated telemetry folder, applied with ``/grant``.

    ``icacls <dir> /inheritance:r`` cannot be used here: without ``/T`` it rewrites
    only the folder, so every child keeps ACEs flagged ``(I)`` for an inheritance
    that no longer exists - including the ``(IO)`` *inherit-only* entries, which
    grant nothing to the child itself. A child that had no explicit ACE of its own
    (the sequence state created privately by ``SequenceStore``) then ends up with no
    effective rights at all, and the publisher's next ``os.replace`` fails with
    WinError 5.

    Instead the folder's current trustees (as SIDs, from SDDL) plus the owner and
    Administrators are granted explicitly to every child, and then to the folder
    recursively, which rewrites the ACE inheritance flags so each child actually
    holds the rights it previously inherited. Children are updated before the folder
    so a mid-way failure leaves inheritance intact rather than a partial state.

    Returns True when the folder was hardened.
    """
    folder_entries = _acl_entries(folder)
    if not folder_entries:
        logger.debug("could not read the ACL of %s; leaving it unhardened", folder)
        return False
    try:
        children = [child for child in folder.iterdir() if not child.is_symlink()]
    except OSError as exc:
        logger.debug(
            "telemetry directory enumeration denied for %s (%s)", folder, type(exc).__name__
        )
        return False

    # A dedicated telemetry folder carries only the operating-system trustees that
    # the observation keeps at the observation file. Those are named explicitly so
    # icacls receives permission *symbols*: it rejects raw SDDL rights strings such as
    # ``FA`` in ``/grant`` (exit code 87, "Invalid parameter"). Anything else that
    # appears on the folder is deliberately not re-granted.
    trustees = _LOCAL_TRUSTEE_SYMBOLS
    grants: list[str] = [f"{owner}:(M,DE)", "Administrators:(F)"]
    for sid, _rights in folder_entries:
        if sid in trustees:
            grants.append(f"{trustees[sid]}:(F)")

    def apply(target: Path, *, recursive: bool) -> bool:
        try:
            result = subprocess.run(
                [
                    "icacls", str(target), "/grant", *grants,
                    *(["/T", "/C"] if recursive else []),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("ACL apply skipped for %s (%s)", target, type(exc).__name__)
            return False
        if result.returncode != 0:
            logger.warning(
                "could not apply least-privilege ACL to %s (%s)",
                target,
                "icacls_rejected" if result.returncode > 0 else "icacls_error",
            )
        return result.returncode == 0

    # Children first, then the folder itself. If a child cannot be granted, stop:
    # hardening the folder afterwards would strip the rights that child still
    # relies on through inheritance.
    for child in children:
        if not apply(child, recursive=False):
            return False
    return apply(folder, recursive=True)


def apply_file_protection(path: Path | str) -> None:
    """Apply least-privilege ACLs to the telemetry file and its folder.

    Windows: the file's inherited ACEs are replaced with explicit entries for the
    local Administrators group and the owning account. A dedicated telemetry folder
    is hardened further (see ``_protect_windows_directory``); a shared parent is
    never touched.

    The owner receives ``(M,DE)`` - modify plus delete - not ``(R)`` (read-only).
    ``publish_telemetry`` replaces the file on every publish (``os.replace``); an
    owner restricted to read access cannot perform that replace and the *publisher
    itself* would fail with ``PermissionError`` on the second observation. Delete is
    required because that is the right ``os.replace`` consumes on the target file.
    Neither right is Full Control: the owner still cannot rewrite the ACL to admit
    another principal, and Everyone / Users / Authenticated Users are denied.

    Non-Windows platforms fall back to owner-only mode bits.

    Failures are logged, not raised, so a publisher running without elevation can
    still emit the file (operators must harden ACLs separately where required).
    """
    target = Path(path)
    # Harden the file itself. Only harden a folder when it is a dedicated telemetry
    # directory created for this file - never a caller-supplied parent such as a
    # shared temp root, because revoking inheritance there breaks the owner's own
    # access to unrelated files (Windows removes the rights that the OS and test
    # cleanup need to traverse and delete the directory).
    folder = target.parent if _is_telemetry_directory(target.parent) else None
    owner = os.environ.get("USERNAME") or os.environ.get("USER") or "owner"
    # Invoke icacls.exe directly (not via powershell -Command) so that the
    # permission shorthand such as :(M) and :(F) is not mangled by PowerShell
    # history expansion. Each command is run as a list so no shell parsing occurs.
    #
    # Order matters on Windows: "/inheritance:r" on the folder strips the inherited
    # ACEs from every child. A child that has no explicit ACE of its own ends up
    # with an EMPTY ACL - not even the owner can open or replace it. The file is
    # therefore granted its explicit owner/Administrators ACEs *before* the folder
    # inheritance is revoked, so it never depends on the vanished inherited entry.
    commands: list[list[str]] = [
        # DELETE is granted explicitly: the publisher replaces its own observation on
        # every publish (``os.replace``), and a Windows file's explicit DELETE right
        # is what ``os.replace`` needs in addition to ``FILE_ADD_FILE`` on the parent.
        # The owner ACE stays (M) + DELETE - it is not Full Control and does not let
        # the owner rewrite the ACL to admit another principal.
        ["icacls", str(target), "/grant", f"{owner}:(M,DE)"],
        ["icacls", str(target), "/grant", "Administrators:(F)"],
        ["icacls", str(target), "/inheritance:r"],
    ]
    for command in commands:
        try:
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("file protection command skipped: %s (%s)", command, exc)
    if folder is not None and os.name == "nt":
        # A dedicated folder is hardened by re-applying its entitlement explicitly to
        # every child first (see ``_protect_windows_directory``); a plain
        # ``/inheritance:r`` on the folder would empty the ACL of any child that had
        # no ACE of its own, which includes files the publisher does not own.
        _protect_windows_directory(folder, owner)
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
    sequence_state = _default_sequence_state(target)
    store = sequence_store or SequenceStore(sequence_state)
    # The observation.sequence is authoritative; store is only advanced on behalf
    # of callers that did not pre-assign a sequence. If no store is provided we
    # still persist the observed sequence for continuity.
    try:
        store.write(observation.sequence)
    except HostTelemetryError:
        logger.warning("could not persist sequence state for %s", target)

    atomic_write_bytes(target, serialized)
    # This also hardens the containing directory when it is a dedicated telemetry
    # folder, so it must run before the state file is hardened (see below).
    apply_file_protection(target)
    # The sequence state is replaced on every publish. Give it the same explicit
    # owner ACE so hardening the folder can never leave it with an empty
    # (owner-less) ACL. Only the canonical state file is hardened: unrelated
    # operator-authored *.json beside the observation keeps the directory's
    # inherited ACL.
    #
    # Order matters when the directory is itself hardened: a folder ACE of (M) also
    # caps the rights the owner holds on a child, even one whose own ACE says (F).
    # Hardening the state file *before* the folder would strip the owner's DELETE
    # right and break ``SequenceStore.write`` on the NEXT publish with WinError 5.
    # It is therefore hardened as the last step of the publish, after its own write
    # and after the folder's ACL, and nothing writes to it afterwards.
    if sequence_state.exists() and _is_canonical_sequence_state(sequence_state):
        apply_file_protection(sequence_state)
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
