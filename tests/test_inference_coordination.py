import threading
import time

import pytest

from app import inference_coordination as ic


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.lock = threading.Lock()

    def set(self, key, value, nx=False, ex=None):
        with self.lock:
            if nx and key in self.store:
                return False
            self.store[key] = value
            return True

    def get(self, key):
        with self.lock:
            return self.store.get(key)

    def eval(self, script, numkeys, key, *args):
        owner = args[0]
        with self.lock:
            if self.store.get(key) != owner:
                return 0
            if "del" in script:
                del self.store[key]
                return 1
            return 1

    def close(self):
        return None


def _allow_resources(monkeypatch, now=1_000_000.0):
    monkeypatch.setenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", "true")
    monkeypatch.setenv("INFERENCE_TELEMETRY_COLLECTED_AT", str(now))
    monkeypatch.setenv("INFERENCE_TRUSTED_AVAILABLE_RAM_BYTES", str(8 * 1024**3))
    monkeypatch.setenv("INFERENCE_TRUSTED_GPU_CAPACITY_OK", "true")
    monkeypatch.setenv("INFERENCE_TELEMETRY_MAX_AGE_SECONDS", "30")
    monkeypatch.setattr(ic.time, "time", lambda: now)


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

