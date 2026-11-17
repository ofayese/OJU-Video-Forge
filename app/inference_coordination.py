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
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

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


def read_trusted_telemetry(now: float | None = None) -> TrustedTelemetry:
    """Read fresh trusted telemetry or fail closed.

    Preferred source: signed JSON envelope in INFERENCE_TELEMETRY_JSON with
    INFERENCE_TELEMETRY_HMAC_KEY for cross-boundary integrity validation.
    For local/dev compatibility only, unsigned env measurements may be used when
    INFERENCE_TELEMETRY_ALLOW_UNSIGNED=true; they are still treated as static
    unless collected_at/max_age_seconds prove freshness.
    """
    now = time.time() if now is None else now
    max_age_default = _telemetry_max_age_seconds()
    allowed_clock_skew_seconds = max(0, _parse_int("INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS") or 5)
    key = _expected_telemetry_key()
    envelope_raw = os.environ.get("INFERENCE_TELEMETRY_JSON")
    if envelope_raw:
        try:
            envelope = json.loads(envelope_raw)
        except json.JSONDecodeError as exc:
            raise InferenceCoordinationError("inference_telemetry_invalid") from exc
        if not isinstance(envelope, dict):
            raise InferenceCoordinationError("inference_telemetry_invalid")
        signature = envelope.get("signature")
        payload = envelope.get("payload")
        if not isinstance(signature, str) or not isinstance(payload, dict):
            raise InferenceCoordinationError("inference_telemetry_invalid")
        if key is None or not _verify_telemetry_signature(payload, signature, key):
            raise InferenceCoordinationError("inference_telemetry_integrity_unverified")
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
        except (KeyError, TypeError, ValueError) as exc:
            raise InferenceCoordinationError("inference_telemetry_invalid") from exc
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
        return TrustedTelemetry(
            available_ram_bytes=available_ram,
            gpu_free_bytes=gpu_free_int,
            gpu_total_bytes=gpu_total_int,
            gpu_capacity_ok=gpu_capacity_ok,
            collected_at=collected_at,
            max_age_seconds=max_age,
            source_id=source_id,
            integrity_verified=True,
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
