"""Synthetic, offline smoke check for the packaged CPU narration runtime."""

import json
import os
import signal
import tempfile
import time
import traceback
from pathlib import Path

import ctranslate2
import faster_whisper

from app import narration


def _mount_options(mountpoint: str) -> set[str]:
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if fields[4].replace("\\040", " ") == mountpoint:
            return set(fields[5].split(","))
    raise RuntimeError(f"mount_not_found:{mountpoint}")


def _security_controls(model: Path) -> dict:
    status = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("CapEff:", "NoNewPrivs:")):
            key, value = line.split(":", 1)
            status[key] = value.strip()

    uid = os.geteuid()
    gid = os.getegid()
    capabilities = int(status["CapEff"], 16)
    if uid != 10001 or gid != 10001 or capabilities != 0:
        raise RuntimeError("unexpected_user_or_capabilities")
    if status["NoNewPrivs"] != "1":
        raise RuntimeError("no_new_privileges_not_set")

    root_options = _mount_options("/")
    model_options = _mount_options("/models/asr")
    tmp_options = _mount_options("/tmp")
    if "ro" not in root_options or "ro" not in model_options:
        raise RuntimeError("root_or_model_mount_is_writable")
    if not {"noexec", "nosuid"}.issubset(tmp_options):
        raise RuntimeError("temporary_filesystem_not_restricted")

    cgroup = Path("/sys/fs/cgroup")
    cpu_quota, cpu_period = (cgroup / "cpu.max").read_text().split()
    memory_limit = int((cgroup / "memory.max").read_text())
    process_limit = int((cgroup / "pids.max").read_text())
    if cpu_quota == "max" or int(cpu_quota) > 2 * int(cpu_period):
        raise RuntimeError("cpu_limit_exceeds_two_cores")
    if memory_limit <= 0 or memory_limit > 2 * 1024**3:
        raise RuntimeError("memory_limit_exceeds_two_gib")
    if process_limit <= 0 or process_limit > 128:
        raise RuntimeError("process_limit_exceeds_128")

    interfaces = sorted(item.name for item in Path("/sys/class/net").iterdir())
    if interfaces != ["lo"]:
        raise RuntimeError("network_namespace_has_non_loopback_interface")

    if model.is_symlink() or not model.is_dir() or not (model / "model.bin").is_file():
        raise RuntimeError("local_asr_model_missing_or_untrusted")
    probe = model / ".ovf-read-only-probe"
    try:
        with probe.open("xb"):
            pass
    except OSError:
        pass
    else:
        probe.unlink(missing_ok=True)
        raise RuntimeError("model_volume_is_writable")

    database_env_markers = ("DATABASE", "POSTGRES", "REDIS", "SQLALCHEMY")
    if any(
        any(marker in key.upper() for marker in database_env_markers)
        or key.upper().startswith(("DB_", "PGPASSWORD", "PGUSER"))
        for key in os.environ
    ):
        raise RuntimeError("application_credentials_present")
    if Path("/var/run/docker.sock").exists():
        raise RuntimeError("docker_socket_present")

    return {
        "uid": uid,
        "gid": gid,
        "effective_capabilities": f"{capabilities:016x}",
        "no_new_privileges": True,
        "root_read_only": True,
        "model_volume_read_only": True,
        "model_volume_write_probe": "DENIED",
        "tmpfs_options": sorted(tmp_options),
        "network_interfaces": interfaces,
        "cgroup_limits": {
            "cpu_quota": cpu_quota,
            "cpu_period": int(cpu_period),
            "memory_bytes": memory_limit,
            "pids": process_limit,
        },
        "database_credentials_present": False,
        "docker_socket_present": False,
    }


def _write_synthetic_source(directory: Path, speech: Path, check) -> Path:
    input_video = Path(os.environ["OVF_SMOKE_VIDEO"])
    if input_video.is_symlink() or not input_video.is_file():
        raise RuntimeError("synthetic_video_fixture_missing_or_untrusted")
    fixture_hash = narration.digest(input_video)
    if fixture_hash != os.environ["OVF_SMOKE_VIDEO_SHA256"]:
        raise RuntimeError("synthetic_video_fixture_checksum_mismatch")
    check()
    source = directory / "source.mp4"
    narration.run([
        "ffmpeg", "-nostdin", "-v", "error",
        "-i", str(input_video), "-i", str(speech),
        "-map", "0:v:0", "-map", "1:a:0", "-t", "6",
        "-c:v", "copy",
        "-c:a", "aac", "-threads", "2", str(source),
    ], check, timeout=60)
    return source


def _check():
    time_limit = int(os.environ.get("OVF_SMOKE_TIME_LIMIT_SECONDS", "300"))
    if not 60 <= time_limit <= 600:
        raise ValueError("smoke_time_limit_must_be_between_60_and_600_seconds")

    started = time.monotonic()
    deadline = started + time_limit

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("smoke_execution_time_limit_exceeded")

    def timed_out(_signum, _frame):
        raise TimeoutError("smoke_execution_time_limit_exceeded")

    signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, time_limit)

    report = {
        "status": "FAIL",
        "time_limit_seconds": time_limit,
        "checks": {},
    }
    try:
        check()
        model = Path(os.environ["NARRATION_ASR_MODEL"])
        report["isolation"] = _security_controls(model)
        options = narration.NarrationOptions(
            analysis_mode="transcript", audio_mode="mix", original_volume=20,
        )
        script = [{
            "id": "synthetic",
            "start_ms": 0,
            "end_ms": 6000,
            "text": "This is a synthetic narration security test.",
        }]
        with tempfile.TemporaryDirectory(prefix="narration-security-smoke-") as work:
            directory = Path(work)
            audio = narration.synthesize(script, directory, options, 6, check)
            source = _write_synthetic_source(directory, audio, check)
            report["synthetic_fixture_sha256"] = narration.digest(
                Path(os.environ["OVF_SMOKE_VIDEO"]),
            )
            source_hash = narration.digest(source)
            metadata = narration.probe(source, check)
            if not any(
                stream["codec_name"] == "h264" and stream["codec_type"] == "video"
                for stream in metadata["streams"]
            ):
                raise RuntimeError("synthetic_source_h264_not_found")
            if not any(
                stream["codec_name"] == "aac" and stream["codec_type"] == "audio"
                for stream in metadata["streams"]
            ):
                raise RuntimeError("synthetic_source_aac_not_found")

            frames = narration.scenes(
                source, directory, metadata, options, check,
            )
            recognized = " ".join(frame["ocr"] or "" for frame in frames)
            if "SYNTHETIC" not in recognized or "SECURITY" not in recognized:
                raise RuntimeError(f"synthetic_title_ocr_failed:{recognized.strip()}")
            report["checks"]["tesseract_english_ocr"] = "PASS"
            report["ocr_text"] = recognized.strip()

            transcript = narration.transcribe(source, directory, metadata, check)
            text = " ".join(segment["text"] for segment in transcript["segments"])
            if transcript["status"] != "transcribed" or "security" not in text.lower():
                raise RuntimeError(f"synthetic_speech_not_recognized:{text}")
            duration_ms = round(float(metadata["format"]["duration"]) * 1000)
            if any(
                not 0 <= segment["start_ms"] < segment["end_ms"] <= duration_ms + 150
                for segment in transcript["segments"]
            ):
                raise RuntimeError("transcription_timestamps_out_of_clip")
            report["checks"]["faster_whisper_cpu_int8"] = "PASS"
            report["transcript"] = {
                "language": transcript["language"],
                "segments": transcript["segments"],
                "provider": transcript.get("provider"),
                "model_revision": transcript.get("model_revision"),
            }

            mixed_output = directory / "mixed.mp4"
            mixed_metadata = narration.mux(
                source, audio, mixed_output, metadata, options, check,
            )
            if not any(
                stream["codec_type"] == "audio"
                for stream in mixed_metadata["streams"]
            ):
                raise RuntimeError("mixed_audio_stream_missing")

            preserved_output = directory / "preserved.mp4"
            preserve_options = narration.NarrationOptions(audio_mode="preserve")
            narration.mux(
                source, None, preserved_output, metadata, preserve_options, check,
            )
            if narration.video_hash(source, check) != narration.video_hash(mixed_output, check):
                raise RuntimeError("mixed_render_source_video_changed")
            if narration.video_hash(source, check) != narration.video_hash(preserved_output, check):
                raise RuntimeError("preserved_source_video_changed")
            if narration.audio_hash(source, check) != narration.audio_hash(preserved_output, check):
                raise RuntimeError("preserved_source_audio_changed")
            if narration.digest(source) != source_hash:
                raise RuntimeError("source_bytes_changed")

            report["checks"].update({
                "ffprobe_h264_aac": "PASS",
                "ffmpeg_mix_render": "PASS",
                "espeak_synthesis": "PASS",
                "source_video_stream_preservation": "PASS",
                "source_audio_stream_preservation": "PASS",
                "source_bytes_unchanged": "PASS",
            })
            report["source"] = {
                "duration_seconds": float(metadata["format"]["duration"]),
                "sha256": source_hash,
            }
            report["mixed_output"] = {
                "bytes": mixed_output.stat().st_size,
                "sha256": narration.digest(mixed_output),
            }
            report["preserved_output"] = {
                "bytes": preserved_output.stat().st_size,
                "sha256": narration.digest(preserved_output),
            }
            report["runtime"] = {
                "faster_whisper": faster_whisper.__version__,
                "ctranslate2": ctranslate2.__version__,
                "cpu_compute_types": sorted(
                    ctranslate2.get_supported_compute_types("cpu"),
                ),
            }
            report["status"] = "PASS"
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            check()
        return report
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main():
    try:
        report = _check()
    except Exception as exc:
        print(json.dumps({
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, sort_keys=True))
        traceback.print_exc()
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
