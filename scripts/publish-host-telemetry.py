r"""Production telemetry publisher for OJU Video Forge.

Collects real Windows host telemetry, signs it with the HMAC key, and publishes
atomically to the observation file for consumption by API/worker containers via
the read-only bind mount.

Supports two modes:

  * single-shot (default): collect, sign, publish once, exit. Used for manual
    verification and by an external scheduler that controls cadence.
  * daemon (--daemon): run forever, publishing once every
    PUBLISH_INTERVAL_SECONDS (default 10s). Acquires an exclusive file lock on
    the publisher lock file so overlapping runs are impossible; if a previous
    instance is still holding the lock, this instance exits immediately.

Designed to run under Windows Task Scheduler as the ERE telemetry publisher.
Task registration requires an administrator (current interactive user is not
elevated); this script never attempts self-registration.

Security boundaries:
  * The HMAC signing key is read from a file outside the observation directory
    (INFERENCE_TELEMETRY_HMAC_KEY_FILE). The key value is never on the command
    line and never in an environment variable visible to consumers.
  * The observation directory (containing observation.json and publisher
    sequence state) is bind-mounted read-only into consumers. The key file lives
    in a separate secrets directory and is mounted independently.
  * Overlapping runs are prevented by an exclusive lock on
    <output_dir>/publisher.lock.
"""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.host_telemetry import (
    SequenceStore,
    collect_telemetry,
    load_envelope,
    publish_telemetry,
)
from app.inference_coordination import (
    InferenceCoordinationError,
    read_trusted_telemetry,
)

# --- Directory layout -------------------------------------------------------
# Observation output directory: contains observation.json. This directory is
# bind-mounted read-only into consumers. The apply_file_protection() call in
# publish_telemetry() disables inheritance on this folder, so the publisher's
# own mutable state (sequence counter, lock file) must live in a separate
# SIBLING directory (not a child of OBS_DIR) to avoid collateral ACL damage.
OUTPUT_DIR = Path(os.environ.get(
    "TELEMETRY_OUTPUT_DIR",
    r"C:\ProgramData\OJU Video Forge\telemetry",
))
OUTPUT_FILE = OUTPUT_DIR / "observation.json"

# Publisher's durable sequence state lives in a sibling directory so that
# apply_file_protection() on the observation file (which disables folder
# inheritance recursively) cannot break subsequent sequence reads/writes.
PUBLISHER_STATE_DIR = OUTPUT_DIR.parent / (OUTPUT_DIR.name + "-state")
PUBLISHER_SEQ_FILE = PUBLISHER_STATE_DIR / ".telemetry-sequence-state.json"

# Legacy sequence state location (pre-correction). Used only to seed the new
# location so the counter is preserved across the directory move.
LEGACY_SEQ_FILE = OUTPUT_DIR / ".telemetry-sequence-state.json"

# Exclusive lock preventing overlapping publisher runs. Lives in the state
# directory (sibling of observation dir) for the same ACL-safety reason.
PUBLISHER_LOCK_FILE = PUBLISHER_STATE_DIR / "publisher.lock"

# Consumer's replay state (_ReplayStore format: {"sources": {...}}).
# Lives on the telemetry-state mount, separate from observation output.
CONSUMER_STATE_FILE = Path(
    os.environ.get(
        "INFERENCE_TELEMETRY_STATE_FILE",
        r"C:\ProgramData\OJU Video Forge\telemetry-state\state.json",
    )
)

# HMAC signing key: read from a file OUTSIDE the observation directory.
# The key file is mounted independently into consumers; consumers never see the
# key storage directory, only the single key file.
KEY_FILE = Path(os.environ.get(
    "INFERENCE_TELEMETRY_HMAC_KEY_FILE",
    r"C:\ProgramData\OJU Video Forge\secrets\hmac.key",
))

# Cadence: target 10 seconds between publications. Satisfies the 30-second
# freshness limit (INFERENCE_TELEMETRY_MAX_AGE_SECONDS) with margin for
# collection time and clock skew.
PUBLISH_INTERVAL_SECONDS = float(os.environ.get(
    "PUBLISH_INTERVAL_SECONDS", "10"
))

SOURCE_ID = os.environ.get("TELEMETRY_SOURCE_ID", "ere-host")

_lock = threading.Lock()
_lock_fd = None


def _read_key_file() -> bytes:
    """Read the HMAC signing key from the file outside the observation dir."""
    key_path = Path(KEY_FILE)
    if not key_path.exists():
        raise InferenceCoordinationError(
            f"HMAC key file not found: {key_path}"
        )
    return key_path.read_bytes()


def _acquire_publisher_lock() -> bool:
    """Try to acquire an exclusive lock on the publisher lock file.

    Returns True if the lock was acquired, False if another instance holds it.
    The lock is bound to this process lifetime and released on exit.
    """
    global _lock_fd
    PUBLISHER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_RDWR | O_CREAT so the lock file is created if missing.
        _lock_fd = os.open(str(PUBLISHER_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        try:
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None
        return False
    return True


def _release_publisher_lock() -> None:
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_lock_fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(_lock_fd)
    except OSError:
        pass
    _lock_fd = None


def publish_once() -> int:
    """Single publication cycle: collect, sign, publish, verify. Returns 0 on success."""
    # Validate signed-only mode (no ALLOW_UNSIGNED)
    if os.environ.get("INFERENCE_TELEMETRY_ALLOW_UNSIGNED", "").lower() in {
        "1", "true", "yes", "on",
    }:
        print("ERROR: INFERENCE_TELEMETRY_ALLOW_UNSIGNED must not be set", file=sys.stderr)
        return 2

    # Read signing key from file outside the observation directory
    key = _read_key_file()

    # Set up environment for the coordinator (consumer-side verification)
    os.environ["INFERENCE_TELEMETRY_FILE"] = str(OUTPUT_FILE)
    os.environ["INFERENCE_TELEMETRY_STATE_FILE"] = str(CONSUMER_STATE_FILE)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PUBLISHER_SEQ_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONSUMER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Seed new sequence state from legacy location if present (preserves counter
    # across the directory move required by apply_file_protection ACL safety).
    if LEGACY_SEQ_FILE.exists() and not PUBLISHER_SEQ_FILE.exists():
        try:
            PUBLISHER_STATE_DIR.mkdir(parents=True, exist_ok=True)
            _legacy = json.loads(LEGACY_SEQ_FILE.read_text(encoding="utf-8"))
            PUBLISHER_SEQ_FILE.write_text(
                json.dumps(_legacy, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        except (OSError, ValueError):
            pass  # start from zero if legacy state is unreadable

    # Get next sequence from durable publisher state (preserves across restarts)
    seq_store = SequenceStore(PUBLISHER_SEQ_FILE)
    seq = seq_store.next()

    # Collect real Windows host telemetry
    obs = collect_telemetry(sequence=seq, source_id=SOURCE_ID)

    # Sign and atomically publish (side effect: writes observation file)
    publish_telemetry(obs, key, OUTPUT_FILE, sequence_store=seq_store)

    # Verify the written file can be loaded and trusted by the consumer path
    load_envelope(OUTPUT_FILE)
    trusted = read_trusted_telemetry()

    print(
        f"PUBLISH OK: seq={trusted.sequence} source={trusted.source_id} "
        f"ram={trusted.available_ram_bytes} bytes "
        f"gpu_free={trusted.gpu_free_bytes} bytes "
        f"capacity_ok={trusted.gpu_capacity_ok} "
        f"integrity_verified={trusted.integrity_verified}"
    )
    return 0


def run_daemon() -> int:
    """Run the publisher in a loop at PUBLISH_INTERVAL_SECONDS cadence.

    Acquires an exclusive lock up front; if another instance holds it, exits.
    Each cycle catches and logs errors but continues to the next interval,
    providing automatic recovery after transient failures. Reboot recovery is
    provided by the Task Scheduler boot trigger (registered by an administrator).
    """
    if not _acquire_publisher_lock():
        print(
            f"PUBLISHER LOCK HELD: another instance is publishing "
            f"({PUBLISHER_LOCK_FILE}); exiting to prevent overlap.",
            file=sys.stderr,
        )
        return 3

    try:
        while True:
            try:
                publish_once()
            except InferenceCoordinationError as exc:
                print(f"PUBLISH FAIL: {exc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"PUBLISH FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            time.sleep(PUBLISH_INTERVAL_SECONDS)
    finally:
        _release_publisher_lock()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OJU host telemetry publisher (single-shot or daemon)"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run continuously at PUBLISH_INTERVAL_SECONDS cadence (default 10s)",
    )
    args = parser.parse_args()

    if args.daemon:
        return run_daemon()
    return publish_once()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InferenceCoordinationError as exc:
        print(f"PUBLISH FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"PUBLISH FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
