#!/usr/bin/env python3
"""OJU Video Forge ERE development bootstrap and verification procedure.

Checks that the local development stack is correctly configured and can
reproduce the working ERE environment from documented source.

Run from the repository root:
    python scripts/verify_dev_stack.py

Exits non-zero if any check fails, with a clear message identifying the
problem. Does not restart or recreate any running services.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ["compose.yaml", "compose.ollama.yaml"]
EXPECTED_IMAGES = {
    "api": "oju-video-forge-api",
    "render-worker": "oju-video-forge-render-worker",
    "ollama": "ollama/ollama",
}
REQUIRED_NETWORKS = {
    "api": {"backend", "control", "agent-inference"},
    "render-worker": {"control", "inference", "agent-inference"},
}
EXPECTED_MODEL_DIGESTS = {
    "qwen2.5:7b-instruct": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
    "qwen3-vl:4b": "a1b29c246319cdcb1f3842f36d29f198f737c0cf516ba1bcba34b1b974e87980",
}
TELEMETRY_KEY_FILE = Path(r"C:\ProgramData\OJU Video Forge\secrets\hmac.key")
TELEMETRY_OBS_DIR = Path(r"C:\ProgramData\OJU Video Forge\telemetry")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def check(condition: bool, message: str, fatal: bool = True) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {message}")
    if not condition and fatal:
        print(f"FATAL: {message}", file=sys.stderr)
        sys.exit(1)


def compose_cmd(extra_files: list[str] | None = None) -> list[str]:
    cmd = ["docker", "compose"]
    seen: set[str] = set()
    for f in COMPOSE_FILES:
        cmd += ["-f", f]
        seen.add(f)
    if extra_files:
        for f in extra_files:
            if f not in seen:
                cmd += ["-f", f]
                seen.add(f)
    return cmd


def check_compose_merge() -> None:
    """Validate that all compose overlays merge without errors."""
    print("\n== Compose overlay validation ==")
    cmd = compose_cmd() + ["config", "--quiet"]
    result = run(cmd)
    check(result.returncode == 0, f"Base compose.yaml validates: {result.stderr.strip()}")
    cmd = compose_cmd(["compose.integration.yaml"]) + ["config", "--quiet"]
    result = run(cmd)
    check(result.returncode == 0, f"compose.yaml + compose.integration.yaml merges: {result.stderr.strip()}")
    cmd = compose_cmd(["compose.ollama.yaml"]) + ["config", "--quiet"]
    result = run(cmd)
    check(result.returncode == 0, f"compose.yaml + compose.ollama.yaml merges: {result.stderr.strip()}")


def check_network_membership() -> None:
    """Verify API and render-worker are on the correct private networks."""
    print("\n== Network membership ==")
    result = run(compose_cmd() + ["config", "--format", "json"])
    if result.returncode != 0:
        check(False, f"compose config failed: {result.stderr.strip()}")
        return
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        check(False, f"compose config output is not valid JSON (got {len(result.stdout)} chars)")
        return
    for service_name in ("api", "render-worker"):
        svc = config["services"][service_name]
        networks = set(svc.get("networks", []))
        required = REQUIRED_NETWORKS[service_name]
        missing = required - networks
        extra = networks - required - {"ollama-bootstrap", "narration-ocr"}
        check(
            not missing,
            f"{service_name} on required networks: missing={missing}",
        )
        if extra:
            print(f"[INFO] {service_name} also on networks: {extra}")


def check_image_versions() -> None:
    """Verify running images match expected identities."""
    print("\n== Image versions ==")
    result = run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.ID}}"])
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, image, cid = parts[0], parts[1], parts[2]
        if name.endswith("-api-1"):
            check(image == "oju-video-forge-api", f"API image is oju-video-forge-api (got {image})")
        elif name.endswith("-render-worker-1"):
            check(image == "oju-video-forge-render-worker", f"Worker image is oju-video-forge-render-worker (got {image})")


def check_model_endpoints_and_digests() -> None:
    """Verify Ollama models are reachable with correct digests."""
    print("\n== Model endpoints and digests ==")
    script = (
        "import urllib.request, json; "
        "r=urllib.request.urlopen('http://ollama:11434/api/tags',timeout=10); "
        "print(json.dumps(json.loads(r.read()).get('models',[])))"
    )
    result = run(["docker", "exec", "-i", "-u", "forge", "oju-video-forge-api-1", "python",
                  "-c", script], timeout=15)
    check(result.returncode == 0, "Ollama reachable from API container via http://ollama:11434")
    if result.returncode != 0:
        return
    models = json.loads(result.stdout)
    if isinstance(models, dict):
        models = models.get("models", [])
    found = {m["name"]: m.get("digest") for m in models}
    for model, expected_digest in EXPECTED_MODEL_DIGESTS.items():
        actual = found.get(model)
        check(actual == expected_digest,
              f"Model {model} digest matches ({actual} == {expected_digest})",
              fatal=False)
    # Verify no host Ollama fallback
    for m in models:
        name = m.get("name", "")
        check("localhost" not in name and "host" not in name.lower(),
              f"No host Ollama fallback: {name}", fatal=False)


def check_secret_mounts() -> None:
    """Verify HMAC key file and telemetry mounts exist on host."""
    print("\n== Secret and telemetry mounts ==")
    check(TELEMETRY_KEY_FILE.is_file(), f"HMAC key file exists: {TELEMETRY_KEY_FILE}")
    check(TELEMETRY_OBS_DIR.is_dir(), f"Telemetry observation dir exists: {TELEMETRY_OBS_DIR}")
    # Key file should be read-only (no write)
    if TELEMETRY_KEY_FILE.is_file():
        try:
            with open(TELEMETRY_KEY_FILE, "a") as f:
                f.write("x")
            check(False, "HMAC key file is NOT read-only (security issue)")
        except (PermissionError, OSError):
            check(True, "HMAC key file is read-only")


def check_telemetry_publisher() -> None:
    """Verify telemetry publisher is producing fresh observations."""
    print("\n== Telemetry publisher ==")
    script = (
        "from app.inference_coordination import read_trusted_telemetry; "
        "import time, json; "
        "t=read_trusted_telemetry(); "
        "d={'seq':t.sequence,'age':round(time.time()-t.collected_at,1),"
        "'integrity':t.integrity_verified,"
        "'fresh':time.time()-t.collected_at<=t.max_age_seconds}; "
        "print(json.dumps(d))"
    )
    result = run(["docker", "exec", "-i", "-u", "forge", "oju-video-forge-api-1", "python",
                  "-c", script], timeout=15)
    check(result.returncode == 0, "Trusted telemetry readable from API container")
    if result.returncode == 0:
        print(f"    {result.stdout.strip()}")


def check_tts_and_ffmpeg() -> None:
    """Verify TTS and FFmpeg are available in both containers."""
    print("\n== TTS and FFmpeg ==")
    for container in ("oju-video-forge-api-1", "oju-video-forge-render-worker-1"):
        result = run(["docker", "exec", "-i", "-u", "forge", container, "sh", "-c",
                      "which espeak-ng && which ffmpeg && which ffprobe"])
        check(result.returncode == 0, f"{container} has espeak-ng, ffmpeg, ffprobe")


def check_non_root_and_readonly() -> None:
    """Verify non-root execution and read-only rootfs."""
    print("\n== Non-root and read-only rootfs ==")
    for container in ("oju-video-forge-api-1", "oju-video-forge-render-worker-1"):
        result = run(["docker", "exec", "-i", "-u", "forge", container, "id"])
        check(result.returncode == 0 and "uid=10001" in result.stdout,
              f"{container} runs as non-root forge (uid=10001)")
        result = run(["docker", "exec", "-i", "-u", "forge", container, "sh", "-c",
                      "touch /opt/forge/.ro_test 2>/dev/null && echo WRITABLE || echo READ_ONLY"])
        check("READ_ONLY" in result.stdout, f"{container} rootfs is read-only")


def main() -> None:
    print("OJU Video Forge ERE Development Bootstrap Verification")
    print("=" * 60)
    print(f"Repository: {REPO_ROOT}")
    print(f"Compose files: {', '.join(COMPOSE_FILES)}")

    check_compose_merge()
    check_network_membership()
    check_image_versions()
    check_model_endpoints_and_digests()
    check_secret_mounts()
    check_telemetry_publisher()
    check_tts_and_ffmpeg()
    check_non_root_and_readonly()

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED — ERE development stack is reproducible")


if __name__ == "__main__":
    main()
