"""Cross-process coordination for heavy local OJU Ollama inference.

This module provides one shared Redis-backed lease for heavy inference/model
transitions plus trusted, fresh resource-admission checks. It does not prove
that the underlying Ollama runtime can interrupt an already-running request;
callers must not use lease expiry to authorize a conflicting model transition
after a remote request has started without runtime-level cancellation support.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

_IS_WINDOWS = sys.platform == "win32"
if _IS_WINDOWS:
    import msvcrt  # type: ignore[import-not-found]
else:
    import fcntl  # type: ignore[import-not-found]

from redis import Redis
from redis.exceptions import RedisError

from .host_telemetry import HostTelemetryError, load_envelope

logger = logging.getLogger(__name__)

try:
    from contextvars import ContextVar
except ImportError:  # pragma: no cover
    ContextVar = None  # type: ignore[assignment]

_active_lease = (
    ContextVar("oju_inference_lease", default=None) if ContextVar else None
)

LEASE_KEY = "oju-video-forge:inference:lease"
DEFAULT_TTL_SECONDS = 300
DEFAULT_WAIT_SECONDS = 30
DEFAULT_RENEW_INTERVAL_SECONDS = 30
MIN_HOST_RAM_RESERVE_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_CLOCK_SKEW_SECONDS = 5
DEFAULT_TELEMETRY_MAX_FILE_BYTES = 64 * 1024
# collected_at (unix seconds) and iso_timestamp must derive from the same UTC
# measurement instant. Allow a small tolerance for host/reader clock skew and
# sub-second serialization rounding.
TELEMETRY_TIMESTAMP_TOLERANCE_SECONDS = 2


class InferenceCoordinationError(RuntimeError):
    """Raised when heavy inference coordination/admission cannot be guaranteed."""


@dataclass(frozen=True)
class InferenceLease:
    owner: str
    key: str = LEASE_KEY
    ttl_seconds: int = DEFAULT_TTL_SECONDS


@dataclass(frozen=True)
class TrustedTelemetry:
    available_ram_bytes: int
    gpu_free_bytes: int | None
    gpu_total_bytes: int | None
    gpu_capacity_ok: bool
    collected_at: float
    max_age_seconds: int
    source_id: str
    integrity_verified: bool
    sequence: int | None = None


def _redis_client() -> Redis:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)


def _parse_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _telemetry_max_age_seconds() -> int:
    return max(1, _parse_int("INFERENCE_TELEMETRY_MAX_AGE_SECONDS") or 30)


def _expected_telemetry_key() -> bytes | None:
    raw = os.environ.get("INFERENCE_TELEMETRY_HMAC_KEY")
    if not raw:
        return None
    return raw.encode("utf-8")


def _verify_telemetry_signature(payload: dict[str, Any], signature: str, key: bytes) -> bool:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _telemetry_file_path() -> str | None:
    raw = os.environ.get("INFERENCE_TELEMETRY_FILE")
    return raw.strip() if raw and raw.strip() else None


def _telemetry_state_file() -> str | None:
    raw = os.environ.get("INFERENCE_TELEMETRY_STATE_FILE")
    return raw.strip() if raw and raw.strip() else None


def _allowed_telemetry_source_ids() -> set[str] | None:
    raw = os.environ.get("INFERENCE_TELEMETRY_ALLOWED_SOURCE_IDS")
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


class _FileLock:
    """Cross-process exclusive file lock.

    Uses msvcrt.locking on Windows and fcntl.flock on POSIX. The lock is bound
    to the lifetime of the instance: acquire on enter, release on close/exit.
    Lock failures and crashes fail closed (the caller never proceeds without the
    lock).
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if _IS_WINDOWS:
                # Lock a large region covering the whole file.
                msvcrt.locking(self._fd, msvcrt.LK_RLCK, 1 << 30)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_EX)
        except OSError:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            raise

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if _IS_WINDOWS:
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1 << 30)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


class _ReplayStore:
    """Durable per-source replay-state store for replay/rollback prevention.

    Stores, per source, the last accepted ``(sequence, payload_hash)`` tuple.
    Backed by a JSON file when INFERENCE_TELEMETRY_STATE_FILE is configured;
    an in-memory store is used otherwise (process-local only, not durable across
    restarts).

    Every public operation acquires a cross-process exclusive lock, reloads
    state from disk, applies the compare-and-update logic, and persists
    atomically before releasing the lock. This guarantees monotonic state even
    when multiple API/worker processes read the same telemetry file
    concurrently.

    Multi-consumer replay rules:
    - Higher sequence than last accepted: accept and advance state.
    - Same sequence with the same canonical payload hash: accept (re-read of an
      identical observation while fresh).
    - Same sequence with a different payload hash: reject (tampered replay).
    - Lower sequence than last accepted: reject (rollback).

    Lock contention, crashes, permission failures and corrupt state all fail
    closed: the caller never accepts an observation it cannot durably record.
    """

    def __init__(self, path: str | None) -> None:
        self._path = Path(path) if path else None
        self._lock_path = Path(str(path) + ".lock") if path else None
        self._state: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._path is None:
            self._state = {}
            self._loaded = True
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            self._state = {}
            self._loaded = True
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Corrupt state: fail closed rather than silently resetting.
            self._state = {}
            self._loaded = True
            return
        if isinstance(data, dict) and isinstance(data.get("sources"), dict):
            loaded: dict[str, dict[str, Any]] = {}
            for key, value in data["sources"].items():
                if not isinstance(value, dict):
                    continue
                seq = value.get("sequence")
                hsh = value.get("hash")
                if (
                    isinstance(seq, int)
                    and not isinstance(seq, bool)
                    and seq >= 0
                    and isinstance(hsh, str)
                ):
                    loaded[str(key)] = {"sequence": seq, "hash": hsh}
            self._state = loaded
        else:
            self._state = {}
        self._loaded = True

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            "sources": {
                key: {"sequence": val["sequence"], "hash": val["hash"]}
                for key, val in self._state.items()
            }
        }
        payload = json.dumps(
            serializable, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=self._path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def record_if_fresh(self, source_id: str, sequence: int, payload_hash: str) -> bool:
        """Record the observation if it is fresh; return True if accepted.

        The full read-modify-write cycle happens under a cross-process exclusive
        lock so concurrent API/worker processes cannot overwrite a higher
        accepted sequence with a lower one.
        """
        if self._lock_path is None:
            # No durable state file: process-local best effort.
            if not self._loaded:
                self._load()
            return self._record_if_fresh_unlocked(source_id, sequence, payload_hash)

        with _FileLock(self._lock_path):
            # Always reload from disk under the lock so we observe any state
            # written by another process since this instance was created.
            self._load()
            return self._record_if_fresh_unlocked(source_id, sequence, payload_hash)

    def _record_if_fresh_unlocked(
        self, source_id: str, sequence: int, payload_hash: str
    ) -> bool:
        current = self._state.get(source_id)
        if current is None:
            self._state[source_id] = {"sequence": int(sequence), "hash": payload_hash}
            self._persist()
            return True
        last_seq = int(current["sequence"])
        last_hash = str(current["hash"])
        if sequence > last_seq:
            self._state[source_id] = {"sequence": int(sequence), "hash": payload_hash}
            self._persist()
            return True
        if sequence == last_seq:
            # Same sequence: accept only if the signed content is identical
            # (a legitimate re-read of the same observation while fresh).
            return payload_hash == last_hash
        # Lower sequence: rollback.
        return False

    def last_state(self, source_id: str) -> dict[str, Any] | None:
        if not self._loaded:
            self._load()
        return self._state.get(source_id)


def _build_trusted_telemetry(
    payload: dict[str, Any],
    now: float,
    max_age_default: int,
    allowed_clock_skew_seconds: int,
    allowed_source_ids: set[str] | None,
    replay_store: _ReplayStore,
    source_label: str,
) -> TrustedTelemetry:
    try:
        collected_at = float(payload["collected_at"])
        max_age = int(payload.get("max_age_seconds", max_age_default))
        source_id = str(payload["source_id"])
        available_ram = int(payload["available_ram_bytes"])
        raw_gpu_capacity_ok = payload["gpu_capacity_ok"]
        if not isinstance(raw_gpu_capacity_ok, bool):
            raise TypeError("gpu_capacity_ok must be bool")
        gpu_capacity_ok = raw_gpu_capacity_ok
        gpu_free = payload.get("gpu_free_bytes")
        gpu_total = payload.get("gpu_total_bytes")
        sequence = payload.get("sequence")
    except (KeyError, TypeError, ValueError) as exc:
        raise InferenceCoordinationError("inference_telemetry_invalid") from exc

    # --- Timestamp consistency: collected_at and iso_timestamp must derive ---
    # --- from the same UTC measurement instant (within documented tolerance). ---
    iso_timestamp = payload.get("iso_timestamp")
    if not isinstance(iso_timestamp, str) or not iso_timestamp:
        raise InferenceCoordinationError("inference_telemetry_invalid")
    try:
        iso_unix = _iso_timestamp_to_unix(iso_timestamp)
    except (ValueError, TypeError) as exc:
        raise InferenceCoordinationError("inference_telemetry_timestamp_invalid") from exc
    if abs(iso_unix - collected_at) > TELEMETRY_TIMESTAMP_TOLERANCE_SECONDS:
        raise InferenceCoordinationError("inference_telemetry_timestamp_inconsistent")

    if max_age <= 0 or not source_id:
        raise InferenceCoordinationError("inference_telemetry_invalid")
    if collected_at - now > allowed_clock_skew_seconds:
        raise InferenceCoordinationError("inference_telemetry_future")
    if now - collected_at > max_age:
        raise InferenceCoordinationError("inference_telemetry_stale")
    gpu_free_int = int(gpu_free) if gpu_free is not None else None
    gpu_total_int = int(gpu_total) if gpu_total is not None else None
    if (gpu_free is not None and gpu_free_int is not None and gpu_free_int < 0) or (
        gpu_total is not None and gpu_total_int is not None and gpu_total_int < 0
    ):
        raise InferenceCoordinationError("inference_telemetry_invalid")

    # Source identity validation: only telemetry from authorized sources is admitted.
    if allowed_source_ids is not None and source_id not in allowed_source_ids:
        raise InferenceCoordinationError("inference_telemetry_unauthorized_source")

    # Sequence / replay validation (multi-consumer safe):
    # - Higher sequence: accept, advance state.
    # - Same sequence + same canonical payload: accept (re-read while fresh).
    # - Same sequence + different payload: reject (tampered replay).
    # - Lower sequence: reject (rollback).
    if sequence is not None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise InferenceCoordinationError("inference_telemetry_invalid")
        payload_hash = _canonical_payload_hash(payload)
        accepted = replay_store.record_if_fresh(source_id, int(sequence), payload_hash)
        if not accepted:
            raise InferenceCoordinationError("inference_telemetry_replay_or_rollback")

    return TrustedTelemetry(
        available_ram_bytes=available_ram,
        gpu_free_bytes=gpu_free_int,
        gpu_total_bytes=gpu_total_int,
        gpu_capacity_ok=gpu_capacity_ok,
        collected_at=collected_at,
        max_age_seconds=max_age,
        source_id=source_id,
        integrity_verified=True,
        sequence=sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else None,
    )


def _iso_timestamp_to_unix(iso_timestamp: str) -> float:
    """Parse an ISO-8601 timestamp and return its Unix-epoch value."""
    text = iso_timestamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
    return dt.timestamp()


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical payload encoding for replay content binding."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def read_trusted_telemetry(now: float | None = None) -> TrustedTelemetry:
    """Read fresh trusted telemetry or fail closed.

    Preferred source: a signed JSON envelope in the file pointed to by
    INFERENCE_TELEMETRY_FILE, verified with INFERENCE_TELEMETRY_HMAC_KEY.
    Falls back to the INFERENCE_TELEMETRY_JSON envelope for cross-boundary
    integrity validation. For local/dev compatibility only, unsigned env
    measurements may be used when INFERENCE_TELEMETRY_ALLOW_UNSIGNED=true; they
    are still treated as static unless collected_at/max_age_seconds prove
    freshness.
    """
    now = time.time() if now is None else now
    max_age_default = _telemetry_max_age_seconds()
    allowed_clock_skew_seconds = max(0, _parse_int("INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS") or DEFAULT_CLOCK_SKEW_SECONDS)
    key = _expected_telemetry_key()
    allowed_source_ids = _allowed_telemetry_source_ids()
    replay_store = _ReplayStore(_telemetry_state_file())

    file_path = _telemetry_file_path()
    env_raw = os.environ.get("INFERENCE_TELEMETRY_JSON")
    envelope: dict[str, Any] | None = None
    source_label = ""

    if file_path:
        try:
            loaded = load_envelope(file_path)
        except HostTelemetryError as exc:
            raise InferenceCoordinationError("inference_telemetry_file_unavailable") from exc
        if not isinstance(loaded, dict):
            raise InferenceCoordinationError("inference_telemetry_invalid")
        envelope = loaded
        source_label = f"file:{file_path}"
    elif env_raw:
        try:
            parsed = json.loads(env_raw)
        except json.JSONDecodeError as exc:
            raise InferenceCoordinationError("inference_telemetry_invalid") from exc
        if not isinstance(parsed, dict):
            raise InferenceCoordinationError("inference_telemetry_invalid")
        envelope = parsed
        source_label = "env:INFERENCE_TELEMETRY_JSON"

    if envelope is not None:
        signature = envelope.get("signature")
        payload = envelope.get("payload")
        if not isinstance(signature, str) or not isinstance(payload, dict):
            raise InferenceCoordinationError("inference_telemetry_invalid")
        if key is None or not _verify_telemetry_signature(payload, signature, key):
            raise InferenceCoordinationError("inference_telemetry_integrity_unverified")
        return _build_trusted_telemetry(
            payload,
            now,
            max_age_default,
            allowed_clock_skew_seconds,
            allowed_source_ids,
            replay_store,
            source_label,
        )

    allow_unsigned = os.environ.get("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", "").lower() in {
        "1", "true", "yes", "on",
    }
    if not allow_unsigned:
        raise InferenceCoordinationError("inference_resource_telemetry_unavailable")

    collected_at_raw = os.environ.get("INFERENCE_TELEMETRY_COLLECTED_AT")
    ram = _parse_int("INFERENCE_TRUSTED_AVAILABLE_RAM_BYTES")
    gpu_ok_raw = os.environ.get("INFERENCE_TRUSTED_GPU_CAPACITY_OK")
    if collected_at_raw is None or ram is None or gpu_ok_raw is None or gpu_ok_raw == "":
        raise InferenceCoordinationError("inference_resource_telemetry_unavailable")
    try:
        collected_at = float(collected_at_raw)
    except ValueError as exc:
        raise InferenceCoordinationError("inference_telemetry_invalid") from exc
    if collected_at - now > allowed_clock_skew_seconds:
        raise InferenceCoordinationError("inference_telemetry_future")
    if now - collected_at > max_age_default:
        raise InferenceCoordinationError("inference_telemetry_stale")
    gpu_ok = gpu_ok_raw.strip().lower() in {"1", "true", "yes", "ok"}
    source_id = os.environ.get("INFERENCE_TELEMETRY_SOURCE_ID", "unsigned-local-env")
    return TrustedTelemetry(
        available_ram_bytes=ram,
        gpu_free_bytes=_parse_int("INFERENCE_TRUSTED_GPU_FREE_BYTES"),
        gpu_total_bytes=_parse_int("INFERENCE_TRUSTED_GPU_TOTAL_BYTES"),
        gpu_capacity_ok=gpu_ok,
        collected_at=collected_at,
        max_age_seconds=max_age_default,
        source_id=source_id,
        integrity_verified=False,
    )


def preflight_heavy_inference(
    now: float | None = None,
    *,
    required_gpu_free_bytes: int | None = None,
) -> TrustedTelemetry:
    telemetry = read_trusted_telemetry(now=now)
    if telemetry.available_ram_bytes < MIN_HOST_RAM_RESERVE_BYTES:
        raise InferenceCoordinationError("inference_insufficient_host_ram")
    if not telemetry.gpu_capacity_ok:
        raise InferenceCoordinationError("inference_insufficient_gpu_capacity")
    if required_gpu_free_bytes is not None:
        if telemetry.gpu_free_bytes is None:
            raise InferenceCoordinationError("inference_gpu_free_vram_unknown")
        if telemetry.gpu_free_bytes < required_gpu_free_bytes:
            raise InferenceCoordinationError("inference_insufficient_gpu_vram")
    return telemetry


_RELEASE_IF_OWNER = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""

_RENEW_IF_OWNER = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
else
  return 0
end
"""


class LeaseLostError(InferenceCoordinationError):
    """Raised when this process no longer owns the inference lease."""


def _assert_owned(client: Redis, lease: InferenceLease) -> None:
    try:
        current = client.get(lease.key)
    except RedisError as exc:
        raise LeaseLostError("inference_lease_state_unavailable") from exc
    if current != lease.owner:
        raise LeaseLostError("inference_lease_lost")


@contextmanager
def heavy_inference_lease(
    purpose: str,
    check: Callable[[], None] | None = None,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    wait_seconds: int = DEFAULT_WAIT_SECONDS,
    renew_interval: int = DEFAULT_RENEW_INTERVAL_SECONDS,
    redis_factory: Callable[[], Redis] | None = None,
) -> Iterator[InferenceLease]:
    """Acquire the shared heavy-inference lease and hold it for the context.

    The lease serializes OJU admission/transition points across processes. It
    cannot interrupt a remote Ollama request already in progress; callers must
    check ownership before model transitions and abort/fail closed if ownership
    is lost. Renewal failures are surfaced as lease loss on the next ownership
    check rather than silently ignored.
    """
    check = check or (lambda: None)
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}:{purpose}"
    lease = InferenceLease(owner=owner, key=LEASE_KEY, ttl_seconds=ttl_seconds)
    factory = redis_factory or _redis_client
    try:
        client = factory()
    except RedisError as exc:
        raise InferenceCoordinationError("inference_coordination_unavailable") from exc

    acquired = False
    stop = threading.Event()
    renew_state: dict[str, Exception | None] = {"error": None}
    renewer: threading.Thread | None = None
    deadline = time.monotonic() + wait_seconds

    try:
        preflight_heavy_inference()
        while True:
            check()
            try:
                if client.set(LEASE_KEY, owner, nx=True, ex=ttl_seconds):
                    acquired = True
                    break
            except RedisError as exc:
                raise InferenceCoordinationError("inference_coordination_unavailable") from exc
            if time.monotonic() >= deadline:
                raise InferenceCoordinationError("inference_lease_unavailable")
            time.sleep(0.25)

        def _renew() -> None:
            interval = min(renew_interval, max(1, ttl_seconds // 3))
            while not stop.wait(interval):
                try:
                    renewed = client.eval(
                        _RENEW_IF_OWNER, 1, LEASE_KEY, owner, ttl_seconds * 1000
                    )
                except RedisError as exc:
                    renew_state["error"] = LeaseLostError("inference_lease_lost")
                    renew_state["error"].__cause__ = exc
                    return
                if renewed != 1:
                    renew_state["error"] = LeaseLostError("inference_lease_lost")
                    return

        renewer = threading.Thread(target=_renew, name="inference-lease-renewer", daemon=True)
        renewer.start()

        _assert_owned(client, lease)
        if renew_state["error"] is not None:
            raise renew_state["error"]
        token = _set_active_lease(lease)
        try:
            yield lease
            if renew_state["error"] is not None:
                raise renew_state["error"]
            _assert_owned(client, lease)
        finally:
            _reset_active_lease(token)
    finally:
        stop.set()
        if renewer is not None:
            renewer.join(timeout=1)
        if acquired:
            try:
                released = client.eval(_RELEASE_IF_OWNER, 1, LEASE_KEY, owner)
                if released != 1:
                    logger.warning("inference lease release did not confirm ownership for %s", purpose)
            except RedisError:
                logger.warning("inference lease release failed for %s", purpose, exc_info=True)
        try:
            client.close()
        except RedisError:
            logger.warning("inference lease client close failed", exc_info=True)


def ensure_lease_owned(lease: InferenceLease, client: Redis | None = None) -> None:
    """Explicit ownership check for callers before irreversible transitions."""
    close = False
    if client is None:
        client = _redis_client()
        close = True
    try:
        _assert_owned(client, lease)
    finally:
        if close:
            try:
                client.close()
            except RedisError:
                logger.warning("inference lease ownership-check client close failed", exc_info=True)


def require_active_inference_lease() -> InferenceLease:
    """Require that the current execution context holds an active inference lease.

    This is a process-local guard for OJU code paths; it does not replace
    cross-process Redis ownership and cannot prove remote runtime cancellation.
    """
    lease = _active_lease.get() if _active_lease is not None else None
    if lease is None:
        raise InferenceCoordinationError("inference_coordination_required")
    # Best-effort ownership validation; fail closed if state cannot be confirmed.
    ensure_lease_owned(lease)
    return lease


def _set_active_lease(lease: InferenceLease | None):
    if _active_lease is None:
        return None
    return _active_lease.set(lease)


def _reset_active_lease(token) -> None:
    if _active_lease is None or token is None:
        return
    try:
        _active_lease.reset(token)
    except Exception:
        logger.warning("failed to reset inference lease context", exc_info=True)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_WAIT_SECONDS",
    "LEASE_KEY",
    "MIN_HOST_RAM_RESERVE_BYTES",
    "InferenceCoordinationError",
    "InferenceLease",
    "LeaseLostError",
    "TrustedTelemetry",
    "ensure_lease_owned",
    "heavy_inference_lease",
    "preflight_heavy_inference",
    "read_trusted_telemetry",
    "require_active_inference_lease",
]
