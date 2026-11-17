import hashlib
import hmac
import json
import os
import threading
import time
from datetime import UTC, datetime

import pytest
from redis.exceptions import RedisError

from app import inference_coordination as ic


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.lock = threading.Lock()
        self.set_failures = 0
        self.get_failures = 0
        self.eval_failures = 0
        self.expire_at = {}

    def _purge_if_expired(self, key):
        expires = self.expire_at.get(key)
        if expires is not None and time.monotonic() >= expires:
            self.store.pop(key, None)
            self.expire_at.pop(key, None)

    def set(self, key, value, nx=False, ex=None):
        if self.set_failures:
            self.set_failures -= 1
            raise RedisError("set failed")
        with self.lock:
            self._purge_if_expired(key)
            if nx and key in self.store:
                return False
            self.store[key] = value
            if ex is not None:
                self.expire_at[key] = time.monotonic() + ex
            elif key in self.expire_at:
                self.expire_at.pop(key, None)
            return True

    def get(self, key):
        if self.get_failures:
            self.get_failures -= 1
            raise RedisError("get failed")
        with self.lock:
            self._purge_if_expired(key)
            return self.store.get(key)

    def eval(self, script, numkeys, key, *args):
        if self.eval_failures:
            self.eval_failures -= 1
            raise RedisError("eval failed")
        owner = args[0]
        with self.lock:
            self._purge_if_expired(key)
            if self.store.get(key) != owner:
                return 0
            if "del" in script:
                del self.store[key]
                self.expire_at.pop(key, None)
                return 1
            if "pexpire" in script:
                ttl_ms = int(args[1])
                self.expire_at[key] = time.monotonic() + (ttl_ms / 1000.0)
                return 1
            return 1

    def close(self):
        return None


def _allow_resources(monkeypatch, now=1_000_000.0):
    monkeypatch.setenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", "true")
    monkeypatch.setenv("INFERENCE_TELEMETRY_COLLECTED_AT", str(now))
    monkeypatch.setenv("INFERENCE_TRUSTED_AVAILABLE_RAM_BYTES", str(8 * 1024**3))
    monkeypatch.setenv("INFERENCE_TRUSTED_GPU_CAPACITY_OK", "true")
    monkeypatch.delenv("INFERENCE_TRUSTED_GPU_FREE_BYTES", raising=False)
    monkeypatch.setenv("INFERENCE_TELEMETRY_MAX_AGE_SECONDS", "30")
    monkeypatch.setattr(ic.time, "time", lambda: now)


def _signed_telemetry(monkeypatch, now=1_000_000.0, **payload_overrides):
    collected_at = payload_overrides.get("collected_at", now)
    payload = {
        "collected_at": collected_at,
        "iso_timestamp": datetime.fromtimestamp(collected_at, tz=UTC).isoformat(),
        "max_age_seconds": 30,
        "source_id": "ere-host:gpu0",
        "available_ram_bytes": 8 * 1024**3,
        "gpu_capacity_ok": True,
        "gpu_free_bytes": 12 * 1024**3,
        "gpu_total_bytes": 16 * 1024**3,
        "measurement_status": "ok",
    }
    payload.update(payload_overrides)
    key = b"test-telemetry-key"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    monkeypatch.setenv("INFERENCE_TELEMETRY_HMAC_KEY", key.decode("utf-8"))
    monkeypatch.setenv(
        "INFERENCE_TELEMETRY_JSON",
        json.dumps({"signature": signature, "payload": payload}, separators=(",", ":")),
    )
    monkeypatch.delenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setattr(ic.time, "time", lambda: now)
    return payload, key, signature


def test_lease_prevents_concurrent_acquire(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    events = []

    def worker(name):
        with ic.heavy_inference_lease(name, wait_seconds=1, ttl_seconds=5, renew_interval=1):
            events.append(f"start-{name}")
            time.sleep(0.2)
            events.append(f"end-{name}")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert events.count("start-a") == 1 and events.count("start-b") == 1
    assert not (
        events.index("start-b") < events.index("end-a")
        and events.index("start-a") < events.index("end-b")
    )


def test_lease_fails_closed_without_telemetry(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    monkeypatch.delenv("INFERENCE_TELEMETRY_JSON", raising=False)
    monkeypatch.delenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", raising=False)
    monkeypatch.delenv("INFERENCE_TELEMETRY_COLLECTED_AT", raising=False)
    monkeypatch.delenv("INFERENCE_TRUSTED_AVAILABLE_RAM_BYTES", raising=False)
    monkeypatch.delenv("INFERENCE_TRUSTED_GPU_CAPACITY_OK", raising=False)
    with pytest.raises(
        ic.InferenceCoordinationError,
        match="telemetry_unavailable|telemetry",
    ), ic.heavy_inference_lease("x", wait_seconds=1):
        pass


def test_lease_releases_on_exception(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    with pytest.raises(ValueError), ic.heavy_inference_lease("x", wait_seconds=1, ttl_seconds=5):
        raise ValueError("boom")
    assert fake.get(ic.LEASE_KEY) is None


def test_preflight_rejects_stale_unsigned_telemetry(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(ic.time, "time", lambda: now)
    monkeypatch.setenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", "true")
    monkeypatch.setenv("INFERENCE_TELEMETRY_COLLECTED_AT", str(now - 120))
    monkeypatch.setenv("INFERENCE_TRUSTED_AVAILABLE_RAM_BYTES", str(8 * 1024**3))
    monkeypatch.setenv("INFERENCE_TRUSTED_GPU_CAPACITY_OK", "true")
    monkeypatch.setenv("INFERENCE_TELEMETRY_MAX_AGE_SECONDS", "30")
    with pytest.raises(ic.InferenceCoordinationError, match="telemetry_stale"):
        ic.preflight_heavy_inference()


def test_ownership_check_detects_lost_lease(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    with (
        pytest.raises(ic.LeaseLostError, match="lease_lost"),
        ic.heavy_inference_lease("x", wait_seconds=1, ttl_seconds=5) as lease,
    ):
        fake.store[ic.LEASE_KEY] = "other-owner"
        with pytest.raises(ic.LeaseLostError, match="lease_lost"):
            ic.ensure_lease_owned(lease, client=fake)
        # context exit must fail closed if ownership was lost mid-operation


def test_signed_telemetry_valid_hmac(monkeypatch):
    _signed_telemetry(monkeypatch)
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.integrity_verified is True
    assert telemetry.source_id == "ere-host:gpu0"
    assert telemetry.gpu_capacity_ok is True
    assert telemetry.available_ram_bytes == 8 * 1024**3


def test_signed_telemetry_rejects_invalid_hmac(monkeypatch):
    _signed_telemetry(monkeypatch)
    raw = json.loads(os.environ["INFERENCE_TELEMETRY_JSON"])
    raw["signature"] = "00" * 32
    monkeypatch.setenv("INFERENCE_TELEMETRY_JSON", json.dumps(raw))
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_signed_telemetry_rejects_missing_hmac_key(monkeypatch):
    _signed_telemetry(monkeypatch)
    monkeypatch.delenv("INFERENCE_TELEMETRY_HMAC_KEY", raising=False)
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_signed_telemetry_rejects_tampered_payload(monkeypatch):
    _signed_telemetry(monkeypatch)
    raw = json.loads(os.environ["INFERENCE_TELEMETRY_JSON"])
    raw["payload"]["gpu_capacity_ok"] = False
    monkeypatch.setenv("INFERENCE_TELEMETRY_JSON", json.dumps(raw))
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_signed_telemetry_rejects_expired_measurement(monkeypatch):
    _signed_telemetry(monkeypatch, now=1_000_000.0, collected_at=999_900.0, max_age_seconds=30)
    monkeypatch.setattr(ic.time, "time", lambda: 1_000_000.0)
    with pytest.raises(ic.InferenceCoordinationError, match="telemetry_stale"):
        ic.read_trusted_telemetry()


def test_signed_telemetry_rejects_future_dated_measurement_beyond_skew(monkeypatch):
    _signed_telemetry(monkeypatch, now=1_000_000.0, collected_at=1_000_100.0, max_age_seconds=30)
    monkeypatch.setattr(ic.time, "time", lambda: 1_000_000.0)
    with pytest.raises(ic.InferenceCoordinationError, match="telemetry_future|telemetry_stale|telemetry_invalid"):
        ic.read_trusted_telemetry()


def test_signed_telemetry_rejects_missing_or_malformed_gpu_fields(monkeypatch):
    _signed_telemetry(monkeypatch)
    raw = json.loads(os.environ["INFERENCE_TELEMETRY_JSON"])
    raw["payload"]["gpu_capacity_ok"] = "maybe"
    payload = raw["payload"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    raw["signature"] = hmac.new(b"test-telemetry-key", canonical, hashlib.sha256).hexdigest()
    monkeypatch.setenv("INFERENCE_TELEMETRY_JSON", json.dumps(raw))
    with pytest.raises(ic.InferenceCoordinationError, match="telemetry_invalid"):
        ic.read_trusted_telemetry()


def test_preflight_rejects_gpu_free_vram_below_required(monkeypatch):
    _signed_telemetry(monkeypatch, gpu_free_bytes=2 * 1024**3)
    with pytest.raises(ic.InferenceCoordinationError, match="insufficient_gpu_vram|gpu"):
        ic.preflight_heavy_inference(required_gpu_free_bytes=6 * 1024**3)


def test_preflight_rejects_unknown_required_gpu_free_vram(monkeypatch):
    _signed_telemetry(monkeypatch)
    raw = json.loads(os.environ["INFERENCE_TELEMETRY_JSON"])
    raw["payload"].pop("gpu_free_bytes", None)
    payload = raw["payload"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    raw["signature"] = hmac.new(b"test-telemetry-key", canonical, hashlib.sha256).hexdigest()
    monkeypatch.setenv("INFERENCE_TELEMETRY_JSON", json.dumps(raw))
    with pytest.raises(ic.InferenceCoordinationError, match="gpu_free_vram_unknown|gpu"):
        ic.preflight_heavy_inference(required_gpu_free_bytes=6 * 1024**3)


def test_preflight_rejects_host_ram_below_4_gib(monkeypatch):
    _signed_telemetry(monkeypatch, available_ram_bytes=2 * 1024**3)
    with pytest.raises(ic.InferenceCoordinationError, match="insufficient_host_ram"):
        ic.preflight_heavy_inference()


def test_lease_fails_closed_on_redis_unavailable(monkeypatch):
    _allow_resources(monkeypatch)

    def boom():
        raise RedisError("redis down")

    monkeypatch.setattr(ic, "_redis_client", boom)
    with pytest.raises(ic.InferenceCoordinationError, match="coordination_unavailable"), ic.heavy_inference_lease(
        "x", wait_seconds=0.1
    ):
        pass


def test_ensure_lease_owned_fails_closed_on_redis_get_error(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    lease = ic.InferenceLease(owner="owner")
    fake.set(ic.LEASE_KEY, "owner")
    fake.get_failures = 1
    with pytest.raises(ic.LeaseLostError, match="lease_state_unavailable|lease_lost"):
        ic.ensure_lease_owned(lease, client=fake)


def test_lease_renewal_failure_surfaces_as_lost_lease(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    with pytest.raises(ic.LeaseLostError, match="lease_lost"), ic.heavy_inference_lease(
        "x", wait_seconds=1, ttl_seconds=2, renew_interval=0.05
    ):
        fake.store[ic.LEASE_KEY] = "stolen"
        time.sleep(0.15)


def test_redis_outage_during_renewal_fails_closed(monkeypatch):
    _allow_resources(monkeypatch)

    class FlakyRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.renew_calls = 0

        def eval(self, script, numkeys, key, *args):
            if "pexpire" in script:
                self.renew_calls += 1
                if self.renew_calls >= 2:
                    raise RedisError("renew outage")
            return super().eval(script, numkeys, key, *args)

    fake = FlakyRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    with pytest.raises(ic.LeaseLostError, match="lease_lost"), ic.heavy_inference_lease(
        "x", wait_seconds=1, ttl_seconds=2, renew_interval=0.05
    ):
        time.sleep(0.2)


def test_cancellation_while_waiting_for_lease(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    fake.set(ic.LEASE_KEY, "held-by-other")
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)

    def cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"), ic.heavy_inference_lease(
        "x", check=cancelled, wait_seconds=5
    ):
        pass
    assert fake.get(ic.LEASE_KEY) == "held-by-other"


def test_crash_recovery_allows_new_lease_after_ttl_expiry(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    # Simulate crashed owner: key exists and will expire shortly in fake time.
    fake.store[ic.LEASE_KEY] = "crashed-owner"
    fake.expire_at[ic.LEASE_KEY] = time.monotonic() + 0.05
    time.sleep(0.08)
    with ic.heavy_inference_lease("recovered", wait_seconds=1, ttl_seconds=2, renew_interval=1):
        assert fake.get(ic.LEASE_KEY) != "crashed-owner"
    assert fake.get(ic.LEASE_KEY) is None


def test_second_worker_cannot_acquire_before_active_owner_releases(monkeypatch):
    _allow_resources(monkeypatch)
    fake = FakeRedis()
    monkeypatch.setattr(ic, "_redis_client", lambda: fake)
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with ic.heavy_inference_lease("holder", wait_seconds=1, ttl_seconds=5, renew_interval=1):
            acquired.set()
            release.wait(2)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(1)
    with pytest.raises(ic.InferenceCoordinationError, match="lease_unavailable"), ic.heavy_inference_lease(
        "second", wait_seconds=0.2, ttl_seconds=1
    ):
        pass
    release.set()
    t.join()


