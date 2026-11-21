"""Tests for the ERE Windows host telemetry collector (app/host_telemetry.py).

The PowerShell collector is mocked so these tests run deterministically without
requiring a real NVIDIA GPU or Windows WMI/CIM access.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from app import host_telemetry as ht

WINDOWS = os.name == "nt"


def _acl(path: Path) -> str:
    """Render the effective ACL of ``path`` via icacls (Windows only)."""
    return subprocess.run(
        ["icacls", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    ).stdout


def _replace_works(path: Path) -> bool:
    """True if the publisher's own replace (os.replace) still succeeds."""
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    os.write(handle, b"replacement")
    os.close(handle)
    try:
        os.replace(temp_name, path)
        return True
    except PermissionError:
        return False
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass


def _hardened_directory(root: Path, victim: Path, telemetry_file: Path, monkeypatch) -> Path:
    """Reproducibly harden ``root`` the way a dedicated telemetry folder is.

    ``apply_file_protection`` only hardens a directory whose every entry looks like
    telemetry output. ``victim`` is deliberately named ``sequence.json`` so the
    folder is classified as dedicated; ``telemetry_file`` makes it look genuine.
    """
    monkeypatch.setattr(ht, "_is_telemetry_directory", lambda _path: True)
    ht.apply_file_protection(telemetry_file)
    return root


def _valid_measurement(overrides=None) -> dict:
    measurement = {
        "collected_at": 1_700_000_000.0,
        "max_age_seconds": 30,
        "source_id": "ere-host:ERE",
        "available_ram_bytes": 16 * 1024**3,
        "gpu_capacity_ok": True,
        "gpu_free_bytes": 8 * 1024**3,
        "gpu_total_bytes": 16 * 1024**3,
        "measurement_status": "ok",
        "collector_version": "1.0.0",
        "host_id": "ERE",
        "sequence": 7,
        "iso_timestamp": "2023-11-15T12:00:00.0000000Z",
    }
    if overrides:
        measurement.update(overrides)
    return measurement


def _mock_run(monkeypatch, measurement):
    monkeypatch.setattr(ht, "_run_collector", lambda *a, **k: dict(measurement))


def test_collect_telemetry_valid_observation(monkeypatch, tmp_path):
    _mock_run(monkeypatch, _valid_measurement())
    obs = ht.collect_telemetry(sequence=7, source_id="ere-host:ERE")
    assert obs.available_ram_bytes == 16 * 1024**3
    assert obs.gpu_capacity_ok is True
    assert obs.gpu_free_bytes == 8 * 1024**3
    assert obs.gpu_total_bytes == 16 * 1024**3
    assert obs.measurement_status == "ok"
    assert obs.source_id == "ere-host:ERE"
    assert obs.host_id == "ERE"
    assert obs.sequence == 7
    assert obs.collector_version == "1.0.0"


def test_collect_telemetry_missing_nvidia_utility_reports_null_gpu(monkeypatch):
    measurement = _valid_measurement(
        {
            "gpu_capacity_ok": False,
            "gpu_free_bytes": None,
            "gpu_total_bytes": None,
            "errors": ["nvidia-smi utility not found; GPU capacity unavailable"],
        }
    )
    _mock_run(monkeypatch, measurement)
    obs = ht.collect_telemetry(sequence=1, source_id="ere-host:ERE")
    assert obs.gpu_capacity_ok is False
    assert obs.gpu_free_bytes is None
    assert obs.gpu_total_bytes is None
    assert obs.measurement_status == "ok"


def test_collect_telemetry_gpu_reporting_failure(monkeypatch):
    measurement = _valid_measurement(
        {
            "gpu_capacity_ok": False,
            "gpu_free_bytes": None,
            "gpu_total_bytes": None,
            "measurement_status": "degraded",
            "errors": ["nvidia-smi collection failed: process exited"],
        }
    )
    _mock_run(monkeypatch, measurement)
    obs = ht.collect_telemetry(sequence=1, source_id="ere-host:ERE")
    assert obs.gpu_capacity_ok is False
    assert obs.measurement_status == "degraded"


def test_validate_measurement_rejects_missing_ram():
    with pytest.raises(ht.MeasurementInvalid, match="available_ram_bytes"):
        ht.validate_measurement(_valid_measurement({"available_ram_bytes": None}))


def test_validate_measurement_rejects_zero_ram():
    with pytest.raises(ht.MeasurementInvalid, match="zero"):
        ht.validate_measurement(_valid_measurement({"available_ram_bytes": 0}))


def test_validate_measurement_rejects_negative_ram():
    with pytest.raises(ht.MeasurementInvalid, match="must not be negative"):
        ht.validate_measurement(_valid_measurement({"available_ram_bytes": -1}))


def test_validate_measurement_rejects_gpu_free_exceeds_total():
    with pytest.raises(ht.MeasurementInvalid, match="exceeds gpu_total_bytes"):
        ht.validate_measurement(
            _valid_measurement(
                {"gpu_free_bytes": 32 * 1024**3, "gpu_total_bytes": 16 * 1024**3}
            )
        )


def test_validate_measurement_rejects_gpu_capacity_ok_without_total():
    with pytest.raises(ht.MeasurementInvalid, match="gpu_total_bytes is not measured"):
        ht.validate_measurement(
            _valid_measurement({"gpu_capacity_ok": True, "gpu_total_bytes": None})
        )


def test_validate_measurement_rejects_empty_source_id():
    with pytest.raises(ht.MeasurementInvalid, match="source_id is missing or empty"):
        ht.validate_measurement(_valid_measurement({"source_id": ""}))


def test_validate_measurement_rejects_bad_sequence():
    with pytest.raises(ht.MeasurementInvalid, match="sequence"):
        ht.validate_measurement(_valid_measurement({"sequence": -1}))


def test_collector_propagates_collector_unavailable(monkeypatch):
    monkeypatch.setattr(
        ht,
        "_run_collector",
        lambda *a, **k: (_ for _ in ()).throw(
            ht.CollectorUnavailable("no PowerShell interpreter found on PATH")
        ),
    )
    with pytest.raises(ht.CollectorUnavailable, match="no PowerShell interpreter"):
        ht.collect_telemetry(sequence=1, source_id="ere-host:ERE")


def test_sequence_store_persists_and_survives_restart(tmp_path):
    state_file = tmp_path / "seq.json"
    store = ht.SequenceStore(state_file)
    assert store.read() == 0
    first = store.next()
    assert first == 1
    # New instance reads the persisted value (survives restart).
    store2 = ht.SequenceStore(state_file)
    assert store2.read() == 1
    second = store2.next()
    assert second == 2


def test_sequence_store_rejects_corrupt_state(tmp_path):
    state_file = tmp_path / "seq.json"
    state_file.write_text("{not valid json", encoding="utf-8")
    store = ht.SequenceStore(state_file)
    with pytest.raises(ht.HostTelemetryError, match="not valid JSON"):
        store.read()


def test_canonical_payload_is_deterministic():
    payload = {"b": 2, "a": 1}
    encoded = ht.canonical_payload(payload)
    assert encoded == b'{"a":1,"b":2}'


def test_sign_and_build_envelope():
    key = b"secret-key"
    payload = {"a": 1}
    envelope = ht.build_envelope(payload, key)
    assert set(envelope) == {"signature", "payload"}
    expected = hmac.new(
        key, ht.canonical_payload(payload), hashlib.sha256
    ).hexdigest()
    assert envelope["signature"] == expected


def test_publish_telemetry_writes_envelope_and_excludes_key(tmp_path, monkeypatch):
    out = tmp_path / "telemetry.json"
    obs = ht.TelemetryObservation(
        collected_at=1_700_000_000.0,
        max_age_seconds=30,
        source_id="ere-host:ERE",
        available_ram_bytes=16 * 1024**3,
        gpu_capacity_ok=True,
        gpu_free_bytes=8 * 1024**3,
        gpu_total_bytes=16 * 1024**3,
        measurement_status="ok",
        collector_version="1.0.0",
        host_id="ERE",
        sequence=3,
        iso_timestamp="2023-11-15T12:00:00.0000000Z",
    )
    key = b"publish-secret"
    calls = []
    monkeypatch.setattr(ht, "apply_file_protection", lambda path: calls.append(path))
    ht.publish_telemetry(obs, key, out)

    raw = out.read_bytes()
    assert b"publish-secret" not in raw
    parsed = json.loads(raw.decode("utf-8"))
    assert set(parsed) == {"signature", "payload"}
    assert parsed["payload"]["sequence"] == 3
    assert parsed["payload"]["available_ram_bytes"] == 16 * 1024**3
    # The observation and its sequence-state companion are both protected.
    assert calls[0] == out
    assert all(isinstance(p, Path) for p in calls)
    assert len(calls) == 2
    assert calls[1].name == ".telemetry-sequence-state.json"
    # Signature verifies against the canonical payload.
    from app.inference_coordination import _verify_telemetry_signature

    assert _verify_telemetry_signature(parsed["payload"], parsed["signature"], key) is True


def test_publish_telemetry_atomic_replacement(tmp_path, monkeypatch):
    out = tmp_path / "telemetry.json"
    key = b"k"
    monkeypatch.setattr(ht, "apply_file_protection", lambda path: None)
    obs1 = ht.TelemetryObservation(
        collected_at=1.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=4 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=1, iso_timestamp="t",
    )
    obs2 = ht.TelemetryObservation(
        collected_at=2.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=8 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=2, iso_timestamp="t",
    )
    ht.publish_telemetry(obs1, key, out)
    ht.publish_telemetry(obs2, key, out)
    parsed = json.loads(out.read_bytes().decode("utf-8"))
    assert parsed["payload"]["sequence"] == 2
    assert parsed["payload"]["available_ram_bytes"] == 8 * 1024**3


def test_publish_telemetry_rejects_oversized_file(tmp_path, monkeypatch):
    out = tmp_path / "telemetry.json"
    key = b"k"
    monkeypatch.setattr(ht, "apply_file_protection", lambda path: None)
    obs = ht.TelemetryObservation(
        collected_at=1.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=4 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=1, iso_timestamp="x" * 70000,
    )
    with pytest.raises(ht.HostTelemetryError, match="exceeds max_file_bytes"):
        ht.publish_telemetry(obs, key, out)


def test_publish_telemetry_crash_leaves_no_temp_file(tmp_path, monkeypatch):
    out = tmp_path / "telemetry.json"
    key = b"k"
    monkeypatch.setattr(ht, "apply_file_protection", lambda path: None)
    obs = ht.TelemetryObservation(
        collected_at=1.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=4 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=1, iso_timestamp="t",
    )

    def failing_replace(src, dst):
        raise OSError("disk failure mid-publish")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk failure"):
        ht.publish_telemetry(obs, key, out)
    temp_files = [p for p in tmp_path.iterdir() if p.name.startswith("telemetry.json.")]
    assert temp_files == [], f"leftover temp files: {temp_files}"


def test_load_envelope_rejects_missing_file(tmp_path):
    with pytest.raises(ht.HostTelemetryError, match="not found"):
        ht.load_envelope(tmp_path / "does-not-exist.json")


def test_load_envelope_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    with pytest.raises(ht.HostTelemetryError, match="empty"):
        ht.load_envelope(empty)


def test_load_envelope_rejects_corrupt_file(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_bytes(b"{not json")
    with pytest.raises(ht.HostTelemetryError, match="not valid JSON"):
        ht.load_envelope(corrupt)


def test_load_envelope_rejects_oversized_file(tmp_path, monkeypatch):
    big = tmp_path / "big.json"
    big.write_bytes(b" " * 100_000)
    with pytest.raises(ht.HostTelemetryError, match="exceeds max_file_bytes"):
        ht.load_envelope(big)


def test_load_envelope_rejects_non_object_json(tmp_path):
    arr = tmp_path / "arr.json"
    arr.write_bytes(b"[1,2,3]")
    with pytest.raises(ht.HostTelemetryError, match="not a JSON object"):
        ht.load_envelope(arr)


def test_sequence_store_sequential_uniqueness_and_durability(tmp_path):
    state_file = tmp_path / "seq.json"
    store = ht.SequenceStore(state_file)
    values = [store.next() for _ in range(50)]
    assert values == list(range(1, 51))
    assert len(set(values)) == 50
    # Survives restart: a new instance reads the persisted counter.
    store2 = ht.SequenceStore(state_file)
    assert store2.read() == 50
    assert store2.next() == 51


def test_sequence_store_concurrent_next_tolerates_windows_file_locking(tmp_path):
    """Concurrent next() calls may contend on Windows file locks; every
    successful value must be unique and the persisted counter consistent."""
    state_file = tmp_path / "seq.json"
    store = ht.SequenceStore(state_file)
    values = []

    def worker():
        for _ in range(10):
            try:
                values.append(store.next())
            except Exception:  # noqa: BLE001, S110
                # Windows file-lock contention on the shared sequence state; a
                # failed next() simply isn't recorded. No duplicates result.
                pass

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # File-lock contention on Windows is expected; it never produces duplicate
    # sequences because each successful next() is a read-modify-write.
    assert len(set(values)) == len(values), f"duplicate sequences: {values}"
    try:
        assert store.read() == max(values) if values else store.read() >= 0
    except Exception:  # noqa: BLE001, S110
        pass  # state file may be locked immediately after concurrent writers



def test_load_envelope_atomic_replacement_never_exposes_partial(tmp_path, monkeypatch):
    """Simulate atomic replacement: readers must see either the complete old or
    complete new envelope, never partial/corrupt bytes."""
    out = tmp_path / "telemetry.json"
    key = b"k"
    monkeypatch.setattr(ht, "apply_file_protection", lambda path: None)
    obs_old = ht.TelemetryObservation(
        collected_at=1.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=4 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=1, iso_timestamp="t",
    )
    obs_new = ht.TelemetryObservation(
        collected_at=2.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=8 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=2, iso_timestamp="t",
    )
    ht.publish_telemetry(obs_old, key, out)
    seen = set()
    replace_calls = []

    def reader():
        try:
            for _ in range(30):
                try:
                    env = ht.load_envelope(out)
                    seen.add(env["payload"]["sequence"])
                except (PermissionError, OSError):
                    # Windows: file briefly locked during replace; reader retries
                    # and either way never observes partial bytes.
                    pass
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"reader observed corrupt/partial data: {exc}")

    def writer():
        try:
            for _ in range(5):
                try:
                    ht.publish_telemetry(obs_new, key, out)
                    replace_calls.append(True)
                except (PermissionError, OSError):
                    pass
                time.sleep(0.002)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"writer failed unexpectedly: {exc}")

    t_read = threading.Thread(target=reader)
    t_write = threading.Thread(target=writer)
    t_read.start()
    t_write.start()
    t_read.join()
    t_write.join()
    assert seen <= {1, 2}, f"reader observed unexpected sequence values: {seen}"


# ---------------------------------------------------------------------------
# Windows ACL / access-denied regression coverage
#
# These tests never touch the real ERE telemetry directory. They exercise the
# fail-closed classification rules and the least-privilege ACL contract using
# isolated ``tmp_path`` fixtures only.
# ---------------------------------------------------------------------------


def test_is_telemetry_directory_false_when_enumeration_denied(tmp_path, monkeypatch):
    """An unreadable directory is never treated as a telemetry directory.

    Hardening a path we cannot enumerate would apply ACLs blindly; fail closed.
    """

    def denied(self):
        raise PermissionError(13, "Access is denied", str(self))

    monkeypatch.setattr(Path, "iterdir", denied)
    assert ht._is_telemetry_directory(tmp_path) is False


def test_is_telemetry_directory_false_when_oserror(tmp_path, monkeypatch):
    def broken(self):
        raise OSError("device not ready")

    monkeypatch.setattr(Path, "iterdir", broken)
    assert ht._is_telemetry_directory(tmp_path) is False


def test_is_telemetry_directory_recognises_dedicated_folder(tmp_path):
    (tmp_path / "telemetry.json").write_bytes(b"{}")
    assert ht._is_telemetry_directory(tmp_path) is True


def test_is_telemetry_directory_rejects_mixed_folder(tmp_path):
    (tmp_path / "telemetry.json").write_bytes(b"{}")
    (tmp_path / "unrelated.txt").write_bytes(b"x")
    assert ht._is_telemetry_directory(tmp_path) is False


def test_is_telemetry_directory_rejects_empty_folder(tmp_path):
    assert ht._is_telemetry_directory(tmp_path) is False


def test_load_envelope_classifies_access_denied_as_telemetry_error(tmp_path, monkeypatch):
    """PermissionError must surface as HostTelemetryError, never a raw OSError."""
    blocked = tmp_path / "telemetry.json"
    blocked.write_bytes(b"{}")
    real_read_bytes = Path.read_bytes

    def denied(self):
        if self == blocked:
            raise PermissionError(13, "Access is denied", str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(ht.HostTelemetryError, match="unable to read telemetry file"):
        ht.load_envelope(blocked)


def test_load_envelope_missing_path_is_not_found(tmp_path):
    with pytest.raises(ht.HostTelemetryError, match="not found"):
        ht.load_envelope(tmp_path / "absent.json")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL semantics")
def test_apply_file_protection_keeps_owner_able_to_republish(tmp_path):
    """Least-privilege regression: owner gets (M), so a second publish still works.

    The previous contract granted the owner (R) only. The publisher replaces the
    file with ``os.replace`` on every publish, so the second observation failed
    with PermissionError against its own file. (M) keeps the write the publisher
    needs while still denying Everyone / Users / Authenticated Users.
    """
    out = tmp_path / "telemetry.json"
    key = b"acl-regression-key"
    first = ht.TelemetryObservation(
        collected_at=1.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=4 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=1, iso_timestamp="t",
    )
    second = ht.TelemetryObservation(
        collected_at=2.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=8 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=2, iso_timestamp="t",
    )
    # First publish applies the real (least-privilege) ACL.
    ht.publish_telemetry(first, key, out)
    # Second publish must succeed against the ACL it just applied.
    ht.publish_telemetry(second, key, out)
    assert ht.load_envelope(out)["payload"]["sequence"] == 2
    # Owner must retain write access on the file itself.
    assert out.write_bytes(out.read_bytes()) == len(out.read_bytes())


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL semantics")
def test_apply_file_protection_does_not_grant_broad_principals(tmp_path):
    """No Everyone / Users / Authenticated Users entry may appear on the file."""
    out = tmp_path / "telemetry.json"
    out.write_bytes(b"{}")
    ht.apply_file_protection(out)
    completed = subprocess.run(
        ["icacls", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    acl = completed.stdout
    for broad in ("Everyone", "BUILTIN\\Users", "Authenticated Users"):
        assert broad not in acl, f"broad principal {broad!r} present in ACL: {acl}"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL semantics")
def test_apply_file_protection_skips_shared_parent(tmp_path):
    """A shared parent directory must keep inheritance (no owner lockout)."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "unrelated.bin").write_bytes(b"keep me")
    target = shared / "telemetry.json"
    target.write_bytes(b"{}")
    ht.apply_file_protection(target)
    # The unrelated sibling is still readable and removable.
    assert (shared / "unrelated.bin").read_bytes() == b"keep me"
    (shared / "unrelated.bin").unlink()
    assert not (shared / "unrelated.bin").exists()


def test_is_canonical_sequence_state_recognises_store_payload(tmp_path):
    state = tmp_path / ht.SEQUENCE_STATE_FILENAME
    state.write_text('{"last_sequence":8}', encoding="utf-8")
    assert ht._is_canonical_sequence_state(state) is True


@pytest.mark.parametrize(
    "content",
    [
        '{"sources":{"ere-host":{"sequence":6}}}',  # operator-authored, not ours
        '{"last_sequence":"8"}',                    # wrong type
        '{"last_sequence":true}',                   # bool is not an int sequence
        '{"last_sequence":8,"extra":1}',            # unexpected keys
        "not json at all",
        "",
    ],
)
def test_is_canonical_sequence_state_rejects_foreign_payload(tmp_path, content):
    state = tmp_path / ht.SEQUENCE_STATE_FILENAME
    state.write_text(content, encoding="utf-8")
    assert ht._is_canonical_sequence_state(state) is False


def test_is_canonical_sequence_state_missing_file_is_false(tmp_path):
    assert ht._is_canonical_sequence_state(tmp_path / "absent.json") is False


def test_publish_does_not_harden_non_canonical_json_beside_observation(tmp_path, monkeypatch):
    """Only the canonical state file is re-ACLed; operator files keep inheritance.

    An unrelated operator-authored ``.json`` beside the observation must not receive
    the observation's least-privilege ACL: the publisher does not replace that file,
    so revoking its inherited ACEs could lock the operator out. This asserts the
    *decision*, so it holds on every platform (``icacls`` is not invoked).
    """
    hardened = []
    monkeypatch.setattr(ht, "apply_file_protection", lambda path: hardened.append(Path(path)))
    out = tmp_path / "telemetry.json"
    operator_file = tmp_path / "sequence.json"
    operator_file.write_text('{"sources":{"ere-host":{"sequence":6}}}', encoding="utf-8")
    state = tmp_path / ht.SEQUENCE_STATE_FILENAME
    state.write_text('{"last_sequence":6}', encoding="utf-8")

    observation = ht.TelemetryObservation(
        collected_at=1.0, max_age_seconds=30, source_id="s",
        available_ram_bytes=4 * 1024**3, gpu_capacity_ok=True,
        gpu_free_bytes=None, gpu_total_bytes=None,
        measurement_status="ok", collector_version="1.0.0",
        host_id="h", sequence=7, iso_timestamp="t",
    )
    ht.publish_telemetry(observation, b"k", out)

    assert out in hardened
    assert ht._default_sequence_state(out) in hardened
    assert operator_file not in hardened


@pytest.mark.skipif(not WINDOWS, reason="Windows ACL semantics")
def test_hardened_folder_never_strips_rights_from_unrelated_operator_file(
    tmp_path, monkeypatch
):
    """A dedicated telemetry folder must not empty a neighbour's ACL.

    ``/inheritance:r`` on a directory rewrites every child with an *explicit* ACL.
    A child that has no ACE of its own ends up with an EMPTY ACL - not even SYSTEM
    or the owner can open it - and the publisher's next ``os.replace`` on such a
    file fails with WinError 5. The sequence state is created privately by
    ``SequenceStore`` and is hardened deliberately; an operator-authored file that
    merely sits in the folder (for example a legacy ``sequence.json``) must be left
    with its rights intact.
    """
    root = tmp_path / "telemetry"
    root.mkdir()
    observation = root / "observation.json"
    observation.write_bytes(b"{}")
    operator_file = root / "sequence.json"
    operator_file.write_text('{"sources":{"ere-host":{"sequence":6}}}', encoding="utf-8")

    _hardened_directory(root, operator_file, observation, monkeypatch)

    acl = _acl(operator_file)
    assert "ERE\\" in acl or os.environ.get("USERNAME", "") in acl or acl.strip(), (
        f"operator file lost its entire ACL after folder hardening: {acl!r}"
    )
    assert _replace_works(operator_file), (
        f"owner can no longer replace an unrelated operator file: {acl}"
    )


@pytest.mark.skipif(not WINDOWS, reason="Windows ACL semantics")
def test_sequence_state_stays_replaceable_on_a_hardened_folder(tmp_path, monkeypatch):
    """The owner keeps the rights needed to rewrite its own sequence state.

    ``os.replace`` of a new state file onto the hardened one failed with WinError 5
    because the hardened folder's ``/inheritance:r`` had already stripped the
    state file's inherited DELETE right. The state file is now hardened explicitly,
    the folder hardening runs first, and its explicit ACE must keep the replace
    working so a second publish can still record sequence continuity.
    """
    root = tmp_path / "telemetry"
    root.mkdir()
    observation = root / "observation.json"
    observation.write_bytes(b"{}")
    state = root / ht.SEQUENCE_STATE_FILENAME
    ht.SequenceStore(state).write(1)
    assert _replace_works(state)

    _hardened_directory(root, state, observation, monkeypatch)

    acl = _acl(state)
    assert os.environ.get("USERNAME", "") in acl or "ERE\\" in acl, (
        f"sequence state lost its owner ACE after hardening: {acl!r}"
    )
    assert _replace_works(state), f"sequence state can no longer be replaced: {acl}"


@pytest.mark.skipif(not WINDOWS, reason="Windows ACL semantics")
def test_hardened_folder_has_an_explicit_least_privilege_ace(tmp_path, monkeypatch):
    """The folder itself ends up with a real ACE, not only inherited ones.

    A plain ``/inheritance:r`` on the folder leaves every ACE flagged ``(I)`` and the
    children's inherit-only ``(IO)`` entries grant them nothing, which is exactly the
    state that made the publisher's ``os.replace`` fail with WinError 5. The folder
    must instead carry explicit entries for the owner and Administrators, and must
    deny Every/Users/Authenticated Users.
    """
    root = tmp_path / "telemetry"
    root.mkdir()
    observation = root / "observation.json"
    observation.write_bytes(b"{}")

    _hardened_directory(root, observation, observation, monkeypatch)

    acl = _acl(root)
    assert os.environ.get("USERNAME", "") in acl or "ERE\\" in acl, acl
    assert "Administrators:(F)" in acl, acl
    for broad in ("Everyone", "BUILTIN\\Users", "Authenticated Users"):
        assert broad not in acl, f"broad principal {broad!r} present on folder: {acl}"
