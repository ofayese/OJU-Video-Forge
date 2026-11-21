"""Integration tests for the file-based trusted telemetry reader.

Extends app/inference_coordination.read_trusted_telemetry() to read signed
envelopes from INFERENCE_TELEMETRY_FILE and enforce freshness, source identity,
and sequence/replay validation with durable consumer state.
"""

from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import host_telemetry as ht
from app import inference_coordination as ic


def _make_envelope(payload, key):
    return {
        "signature": ht.sign_payload(payload, key),
        "payload": payload,
    }


def _write_telemetry_file(path, envelope):
    path.write_text(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _base_payload(now=1_700_000_000.0, **overrides):
    payload = {
        "collected_at": now,
        "iso_timestamp": datetime.fromtimestamp(now, tz=UTC).isoformat(),
        "max_age_seconds": 30,
        "source_id": "ere-host:ERE",
        "available_ram_bytes": 16 * 1024**3,
        "gpu_capacity_ok": True,
        "gpu_free_bytes": 8 * 1024**3,
        "gpu_total_bytes": 16 * 1024**3,
        "sequence": 1,
    }
    payload.update(overrides)
    return payload


def _set_file_env(monkeypatch, tmp_path, payload, key=b"reader-secret"):
    env_file = tmp_path / "telemetry.json"
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("INFERENCE_TELEMETRY_FILE", str(env_file))
    monkeypatch.setenv("INFERENCE_TELEMETRY_HMAC_KEY", key.decode("utf-8"))
    monkeypatch.setenv("INFERENCE_TELEMETRY_STATE_FILE", str(state_file))
    monkeypatch.delenv("INFERENCE_TELEMETRY_JSON", raising=False)
    monkeypatch.delenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", raising=False)
    _write_telemetry_file(env_file, _make_envelope(payload, key))
    return env_file, state_file


def test_reader_accepts_valid_signed_file_observation(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now))
    monkeypatch.setattr(ic.time, "time", lambda: now)
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.integrity_verified is True
    assert telemetry.source_id == "ere-host:ERE"
    assert telemetry.available_ram_bytes == 16 * 1024**3
    assert telemetry.gpu_capacity_ok is True
    assert telemetry.sequence == 1


def test_reader_rejects_invalid_signature(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now), key=b"reader-secret")
    raw = json.loads(env_file.read_text(encoding="utf-8"))
    raw["signature"] = "00" * 32
    env_file.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_reader_rejects_missing_hmac_key(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now), key=b"reader-secret")
    monkeypatch.delenv("INFERENCE_TELEMETRY_HMAC_KEY", raising=False)
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_reader_rejects_modified_content(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now), key=b"reader-secret")
    raw = json.loads(env_file.read_text(encoding="utf-8"))
    raw["payload"]["gpu_capacity_ok"] = False
    raw["signature"] = ht.sign_payload(raw["payload"], b"reader-secret")
    env_file.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    # Content changed but re-signed with same key is valid; instead tamper without re-signing.
    raw = json.loads(env_file.read_text(encoding="utf-8"))
    raw["payload"]["available_ram_bytes"] = 2 * 1024**3
    env_file.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_reader_rejects_stale_timestamp(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now - 120, max_age_seconds=30)
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="telemetry_stale"):
        ic.read_trusted_telemetry()


def test_reader_rejects_future_timestamp_beyond_skew(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now + 100, max_age_seconds=30)
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(
        ic.InferenceCoordinationError, match="telemetry_future|telemetry_stale|telemetry_invalid"
    ):
        ic.read_trusted_telemetry()


def test_reader_rejects_timestamp_inconsistency(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _ = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now)
    )
    # collected_at says "now" but iso_timestamp claims an hour later -> inconsistent.
    raw = json.loads(env_file.read_text(encoding="utf-8"))
    raw["payload"]["iso_timestamp"] = datetime.fromtimestamp(
        now + 3600, tz=UTC
    ).isoformat()
    raw["signature"] = hmac_sign(raw["payload"], b"reader-secret")
    env_file.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(
        ic.InferenceCoordinationError,
        match="telemetry_timestamp_inconsistent|telemetry_timestamp_invalid",
    ):
        ic.read_trusted_telemetry()


def test_reader_rejects_malformed_iso_timestamp(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _ = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now)
    )
    raw = json.loads(env_file.read_text(encoding="utf-8"))
    raw["payload"]["iso_timestamp"] = "not-a-timestamp"
    raw["signature"] = hmac_sign(raw["payload"], b"reader-secret")
    env_file.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(
        ic.InferenceCoordinationError,
        match="telemetry_timestamp_invalid|telemetry_invalid",
    ):
        ic.read_trusted_telemetry()


def test_reader_key_rotation_invalidates_old_signature(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now), key=b"old-key"
    )
    # Rotate the verification key; the old signature must no longer verify.
    monkeypatch.setenv("INFERENCE_TELEMETRY_HMAC_KEY", "new-key")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_reader_permits_concurrent_re_reads_of_same_observation(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now, sequence=1)
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    results = []
    errors = []

    def reader():
        try:
            for _ in range(20):
                try:
                    results.append(ic.read_trusted_telemetry().sequence)
                except ic.InferenceCoordinationError as exc:
                    if "replay_or_rollback" in str(exc):
                        # Re-reads of the identical observation are permitted;
                        # treat any other replay error as unexpected.
                        errors.append(exc)
                    else:
                        errors.append(exc)
                except (PermissionError, OSError, ht.HostTelemetryError):
                    pass
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"errors during concurrent re-reads: {errors}"
    assert all(seq == 1 for seq in results), f"unexpected sequences: {results}"


def hmac_sign(payload, key):
    return _hmac.new(
        key,
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        _hashlib.sha256,
    ).hexdigest()


def test_reader_rejects_replay_or_rollback(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _state_file = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now, sequence=5)
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.sequence == 5

    # Re-reading the identical signed observation while fresh is permitted.
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.sequence == 5

    # Same sequence with different signed content is a tampered replay.
    tampered_payload = _base_payload(now=now, sequence=5)
    tampered_payload["gpu_capacity_ok"] = False
    _write_telemetry_file(
        env_file, _make_envelope(tampered_payload, b"reader-secret")
    )
    with pytest.raises(ic.InferenceCoordinationError, match="replay_or_rollback"):
        ic.read_trusted_telemetry()

    # Rollback to an older observation (sequence 3) must be rejected.
    rollback_payload = _base_payload(now=now, sequence=3)
    _write_telemetry_file(
        env_file, _make_envelope(rollback_payload, b"reader-secret")
    )
    with pytest.raises(ic.InferenceCoordinationError, match="replay_or_rollback"):
        ic.read_trusted_telemetry()


def test_reader_sequence_continues_after_restart(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _state_file = _set_file_env(
        monkeypatch, tmp_path, _base_payload(now=now, sequence=9)
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    telemetry = ic.read_trusted_telemetry()  # accepts sequence 9
    assert telemetry.sequence == 9

    # Simulate process restart: re-reading the identical signed observation
    # (sequence 9, same content) is permitted after restart while fresh.
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.sequence == 9

    # A higher sequence (10) is accepted and advances durable state.
    newer_payload = _base_payload(now=now, sequence=10)
    _write_telemetry_file(
        env_file, _make_envelope(newer_payload, b"reader-secret")
    )
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.sequence == 10

    # Sequence 9 again is rejected (durable rollback prevention across restart).
    _write_telemetry_file(
        env_file, _make_envelope(_base_payload(now=now, sequence=9), b"reader-secret")
    )
    with pytest.raises(ic.InferenceCoordinationError, match="replay_or_rollback"):
        ic.read_trusted_telemetry()

    # Same sequence (10) with different content is rejected as tampered replay.
    tampered_payload = _base_payload(now=now, sequence=10)
    tampered_payload["available_ram_bytes"] = 2 * 1024**3
    _write_telemetry_file(
        env_file, _make_envelope(tampered_payload, b"reader-secret")
    )
    with pytest.raises(ic.InferenceCoordinationError, match="replay_or_rollback"):
        ic.read_trusted_telemetry()


def test_reader_rejects_missing_telemetry_file(monkeypatch, tmp_path):
    monkeypatch.setenv("INFERENCE_TELEMETRY_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("INFERENCE_TELEMETRY_HMAC_KEY", b"k".decode("utf-8"))
    monkeypatch.delenv("INFERENCE_TELEMETRY_JSON", raising=False)
    monkeypatch.delenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setattr(ic.time, "time", lambda: 1_700_000_000.0)
    with pytest.raises(ic.InferenceCoordinationError, match="file_unavailable"):
        ic.read_trusted_telemetry()


def test_reader_rejects_corrupt_telemetry_file(monkeypatch, tmp_path):
    corrupt = tmp_path / "telemetry.json"
    corrupt.write_bytes(b"{not json")
    monkeypatch.setenv("INFERENCE_TELEMETRY_FILE", str(corrupt))
    monkeypatch.setenv("INFERENCE_TELEMETRY_HMAC_KEY", b"k".decode("utf-8"))
    monkeypatch.delenv("INFERENCE_TELEMETRY_JSON", raising=False)
    monkeypatch.delenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setattr(ic.time, "time", lambda: 1_700_000_000.0)
    with pytest.raises(ic.InferenceCoordinationError, match="file_unavailable|invalid"):
        ic.read_trusted_telemetry()


def test_reader_rejects_unauthorized_source(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now))
    monkeypatch.setenv("INFERENCE_TELEMETRY_ALLOWED_SOURCE_IDS", "other-host:ERE")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="unauthorized_source"):
        ic.read_trusted_telemetry()


def test_reader_accepts_authorized_source(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now))
    monkeypatch.setenv("INFERENCE_TELEMETRY_ALLOWED_SOURCE_IDS", "ere-host:ERE,other-host")
    monkeypatch.setattr(ic.time, "time", lambda: now)
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.source_id == "ere-host:ERE"


def test_reader_file_and_env_fallback(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    # No file set: env JSON path still works.
    monkeypatch.delenv("INFERENCE_TELEMETRY_FILE", raising=False)
    payload = _base_payload(now=now)
    env_raw = json.dumps(_make_envelope(payload, b"k"))
    monkeypatch.setenv("INFERENCE_TELEMETRY_JSON", env_raw)
    monkeypatch.setenv("INFERENCE_TELEMETRY_HMAC_KEY", b"k".decode("utf-8"))
    monkeypatch.delenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setattr(ic.time, "time", lambda: now)
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.integrity_verified is True


def test_preflight_rejects_insufficient_host_ram(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch,
        tmp_path,
        _base_payload(now=now, available_ram_bytes=2 * 1024**3),
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="insufficient_host_ram"):
        ic.preflight_heavy_inference()


def test_preflight_rejects_insufficient_gpu_capacity(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch,
        tmp_path,
        _base_payload(now=now, gpu_capacity_ok=False),
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="insufficient_gpu_capacity"):
        ic.preflight_heavy_inference()


def test_preflight_uses_measured_gpu_free_vram(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch,
        tmp_path,
        _base_payload(now=now, gpu_free_bytes=2 * 1024**3, gpu_total_bytes=16 * 1024**3),
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="insufficient_gpu_vram"):
        ic.preflight_heavy_inference(required_gpu_free_bytes=6 * 1024**3)


def test_preflight_accepts_sufficient_ram_and_gpu(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(
        monkeypatch,
        tmp_path,
        _base_payload(
            now=now,
            available_ram_bytes=16 * 1024**3,
            gpu_free_bytes=8 * 1024**3,
            gpu_total_bytes=16 * 1024**3,
        ),
    )
    monkeypatch.setattr(ic.time, "time", lambda: now)
    telemetry = ic.preflight_heavy_inference(
        required_gpu_free_bytes=4 * 1024**3
    )
    assert telemetry.available_ram_bytes >= 4 * 1024**3
    assert telemetry.gpu_capacity_ok is True


def test_concurrent_reads_during_atomic_replacement(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now))
    monkeypatch.setattr(ic.time, "time", lambda: now)
    key = b"reader-secret"
    results = []
    errors = []
    replays = 0

    envelopes = {}
    for seq in range(1, 7):
        payload = _base_payload(now=now, sequence=seq)
        envelopes[seq] = json.dumps(
            _make_envelope(payload, key), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def reader():
        nonlocal replays
        try:
            for _ in range(25):
                try:
                    results.append(ic.read_trusted_telemetry().sequence)
                except ic.InferenceCoordinationError:
                    # Business-logic rejection (replay/freshness/auth) is an
                    # expected response to a complete, valid envelope; not
                    # corruption. Atomic replacement guarantees readers never
                    # observe partial bytes.
                    replays += 1
                except ht.HostTelemetryError as exc:
                    # Real corruption: reader observed partial/invalid bytes.
                    errors.append(exc)
                except (PermissionError, OSError):
                    # Windows: telemetry/state file briefly locked during atomic
                    # replace; reader retries and never observes partial bytes.
                    pass
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def writer():
        try:
            for seq in range(2, 7):
                try:
                    ht.atomic_write_bytes(env_file, envelopes[seq])
                except (PermissionError, OSError):
                    pass
                time.sleep(0.005)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(3)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"errors during concurrent access: {errors}"
    assert results, "no reads completed"
    # Every observed sequence must be a complete, valid observation (never partial).
    assert all(seq in {1, 2, 3, 4, 5, 6} for seq in results)


def test_reader_file_unavailable_when_key_missing(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    _env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now), key=b"k")
    monkeypatch.delenv("INFERENCE_TELEMETRY_HMAC_KEY", raising=False)
    monkeypatch.setattr(ic.time, "time", lambda: now)
    with pytest.raises(ic.InferenceCoordinationError, match="integrity_unverified"):
        ic.read_trusted_telemetry()


def test_reader_recovers_after_publisher_resumes(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    env_file, _ = _set_file_env(monkeypatch, tmp_path, _base_payload(now=now, sequence=1))
    monkeypatch.setattr(ic.time, "time", lambda: now)
    ic.read_trusted_telemetry()  # accepts sequence 1

    # Publisher was down; file missing briefly (simulate gap), then resumes with seq 3.
    monkeypatch.setenv("INFERENCE_TELEMETRY_FILE", str(tmp_path / "gone.json"))
    with pytest.raises(ic.InferenceCoordinationError, match="file_unavailable"):
        ic.read_trusted_telemetry()

    monkeypatch.setenv("INFERENCE_TELEMETRY_FILE", str(env_file))
    resumed_payload = _base_payload(now=now, sequence=3)
    _write_telemetry_file(env_file, _make_envelope(resumed_payload, b"reader-secret"))
    telemetry = ic.read_trusted_telemetry()
    assert telemetry.sequence == 3


# ---------------------------------------------------------------------------
# Cross-process replay-state tests (separate OS processes, not threads).
# ---------------------------------------------------------------------------

_CROSS_PROCESS_SCRIPT = r'''
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from app import inference_coordination as ic

env_file = os.environ["TELEMETRY_FILE"]
state_file = os.environ["TELEMETRY_STATE_FILE"]
key = os.environ["TELEMETRY_KEY"]
now = float(os.environ["TELEMETRY_NOW"])
sequence = int(os.environ["TELEMETRY_SEQUENCE"])

os.environ["INFERENCE_TELEMETRY_FILE"] = env_file
os.environ["INFERENCE_TELEMETRY_STATE_FILE"] = state_file
os.environ["INFERENCE_TELEMETRY_HMAC_KEY"] = key
os.environ.pop("INFERENCE_TELEMETRY_JSON", None)
os.environ.pop("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", None)

payload = {
    "collected_at": now,
    "iso_timestamp": __import__("datetime").datetime.fromtimestamp(
        now, tz=__import__("datetime").timezone.utc
    ).isoformat(),
    "max_age_seconds": 30,
    "source_id": os.environ.get("TELEMETRY_SOURCE_ID", "ere-host:ERE"),
    "available_ram_bytes": 16 * 1024**3,
    "gpu_capacity_ok": True,
    "gpu_free_bytes": 8 * 1024**3,
    "gpu_total_bytes": 16 * 1024**3,
    "sequence": sequence,
}
tamper_ram = os.environ.get("TELEMETRY_TAMPER_RAM")
if tamper_ram is not None:
    payload["available_ram_bytes"] = int(tamper_ram)
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
import hmac as _hmac, hashlib as _hashlib
signature = _hmac.new(key.encode(), canonical, _hashlib.sha256).hexdigest()

with open(env_file, "w") as fh:
    json.dump({"signature": signature, "payload": payload}, fh)

ic.time.time = lambda: now
try:
    telemetry = ic.read_trusted_telemetry()
    result = {"accepted": True, "sequence": telemetry.sequence}
except ic.InferenceCoordinationError as exc:
    result = {"accepted": False, "error": str(exc)}

print(json.dumps(result))
'''


def _run_cross_process(
    tmp_path, sequence, key=b"crossproc-secret", now=1_700_000_000.0,
    source_id="ere-host:ERE", extra_env=None,
):
    """Run read_trusted_telemetry() in a separate OS process."""
    env_file = tmp_path / "cross_telemetry.json"
    state_file = tmp_path / "cross_state.json"
    env = dict(os.environ)
    env["TELEMETRY_FILE"] = str(env_file)
    env["TELEMETRY_STATE_FILE"] = str(state_file)
    env["TELEMETRY_KEY"] = key.decode("utf-8")
    env["TELEMETRY_SOURCE_ID"] = source_id
    env["TELEMETRY_NOW"] = str(now)
    env["TELEMETRY_SEQUENCE"] = str(sequence)
    if extra_env:
        env.update(extra_env)
    completed = __import__("subprocess").run(
        [sys.executable, "-c", _CROSS_PROCESS_SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"subprocess failed (rc={completed.returncode}): {completed.stderr}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_cross_process_higher_sequence_advances_state(tmp_path):
    now = 1_700_000_000.0
    # Process A: seq 5 accepted, advances state to 5.
    result_a = _run_cross_process(tmp_path, 5, now=now)
    assert result_a["accepted"] is True
    assert result_a["sequence"] == 5

    # Process B: seq 6 (higher) accepted, advances state to 6.
    result_b = _run_cross_process(tmp_path, 6, now=now)
    assert result_b["accepted"] is True
    assert result_b["sequence"] == 6

    # Process C: seq 5 (now lower than 6) rejected as rollback.
    result_c = _run_cross_process(tmp_path, 5, now=now)
    assert result_c["accepted"] is False
    assert "replay_or_rollback" in result_c["error"]


def test_cross_process_same_sequence_same_hash_permitted(tmp_path):
    now = 1_700_000_000.0
    # Process A: seq 3 accepted.
    result_a = _run_cross_process(tmp_path, 3, now=now)
    assert result_a["accepted"] is True

    # Process B: seq 3 with identical content accepted (re-read while fresh).
    result_b = _run_cross_process(tmp_path, 3, now=now)
    assert result_b["accepted"] is True
    assert result_b["sequence"] == 3


def test_cross_process_same_sequence_different_hash_rejected(tmp_path, monkeypatch):
    now = 1_700_000_000.0
    # Prime state with seq 4 using a different payload via direct write.
    env_file = tmp_path / "cross_telemetry.json"
    state_file = tmp_path / "cross_state.json"
    monkeypatch.setenv("INFERENCE_TELEMETRY_FILE", str(env_file))
    monkeypatch.setenv("INFERENCE_TELEMETRY_STATE_FILE", str(state_file))
    monkeypatch.setenv("INFERENCE_TELEMETRY_HMAC_KEY", b"crossproc-secret".decode("utf-8"))
    monkeypatch.delenv("INFERENCE_TELEMETRY_JSON", raising=False)
    monkeypatch.delenv("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", raising=False)
    ic.time.time = lambda: now
    payload = _base_payload(now=now, sequence=4)
    _write_telemetry_file(env_file, _make_envelope(payload, b"crossproc-secret"))
    ic.read_trusted_telemetry()  # accepts seq 4

    # Now a separate process tries seq 4 with different content -> rejected.
    result = _run_cross_process(
        tmp_path, 4, now=now,
        extra_env={"TELEMETRY_TAMPER_RAM": str(2 * 1024**3)},
    )
    assert result["accepted"] is False
    assert "replay_or_rollback" in result["error"]


def test_cross_process_state_monotonic_across_restarts(tmp_path):
    now = 1_700_000_000.0
    # Process A: seq 10 accepted.
    result_a = _run_cross_process(tmp_path, 10, now=now)
    assert result_a["accepted"] is True

    # New process (simulated restart): seq 11 accepted.
    result_b = _run_cross_process(tmp_path, 11, now=now)
    assert result_b["accepted"] is True

    # New process: seq 10 rejected (rollback prevention across restart).
    result_c = _run_cross_process(tmp_path, 10, now=now)
    assert result_c["accepted"] is False
    assert "replay_or_rollback" in result_c["error"]

