"""Standalone timeline v1 renderer. No database, network, or worker dependencies."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class RenderError(ValueError):
    """Invalid contract, unsafe media, unavailable tool, or failed verification."""


PROFILES = {
    "preview": (640, 360, 24),
    "hd": (1280, 720, 30),
}
DEMUXERS = {".mp4": "mov", ".mov": "mov", ".mkv": "matroska",
            ".webm": "matroska", ".wav": "wav", ".mp3": "mp3"}
MAX_JSON_BYTES = 65536
MAX_DURATION_MS = 120000
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_PROBE_BYTES = 65536


def _object(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys():
        raise RenderError("Missing required object fields")
    if value.keys() - set(required) - set(optional):
        raise RenderError("Unsupported object fields")
    return value


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise RenderError("Integer outside supported range")
    return value


def _text(value):
    if (type(value) is not str or not 1 <= len(value) <= 200
            or any(not char.isprintable() for char in value)):
        raise RenderError("Text must contain 1-200 printable characters")
    return value


def _path_string(value):
    # Deliberately portable: no drive, URL, UNC, option, traversal, or ADS syntax.
    if (type(value) is not str or len(value) > 240
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./ -]*", value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise RenderError("Media path must be a portable relative path")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RenderError("Duplicate JSON field")
        result[key] = value
    return result


def parse_timeline(payload: str) -> dict:
    """Validate JSON v1; all times are integer milliseconds on the frame grid."""
    if type(payload) is not str or len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise RenderError("Timeline must be bounded JSON text")
    try:
        spec = json.loads(payload, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: None)
    except (ValueError, RecursionError) as exc:
        raise RenderError("Invalid timeline JSON") from exc
    _object(spec, ("version", "clips", "output"), ("title", "captions", "audio"))
    if type(spec["version"]) is not int or spec["version"] != 1:
        raise RenderError("Unsupported timeline version")
    output = _object(spec["output"], ("profile", "format"))
    if (type(output["profile"]) is not str or output["profile"] not in PROFILES
            or output["format"] != "mp4"):
        raise RenderError("Unsupported output profile or format")
    fps = PROFILES[output["profile"]][2]
    clips = spec["clips"]
    if type(clips) is not list or not 1 <= len(clips) <= 16:
        raise RenderError("Expected 1-16 ordered clips")
    total = 0
    for clip in clips:
        _object(clip, ("path", "in_ms", "out_ms"))
        path = _path_string(clip["path"])
        if Path(path).suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
            raise RenderError("Unsupported video container")
        start = _integer(clip["in_ms"], 0, MAX_DURATION_MS)
        end = _integer(clip["out_ms"], start + 1, MAX_DURATION_MS)
        if start * fps % 1000 or end * fps % 1000:
            raise RenderError("Clip times must align to the output frame grid")
        total += end - start
    if total > MAX_DURATION_MS:
        raise RenderError("Timeline exceeds 120 seconds")
    overlays = []
    if "title" in spec:
        overlays.append(spec["title"])
    captions = spec.get("captions", [])
    if type(captions) is not list or len(captions) > 100:
        raise RenderError("Expected at most 100 captions")
    overlays.extend(captions)
    for overlay in overlays:
        _object(overlay, ("text", "start_ms", "end_ms"))
        _text(overlay["text"])
        start = _integer(overlay["start_ms"], 0, total - 1)
        _integer(overlay["end_ms"], start + 1, total)
    previous_end = 0
    for caption in captions:
        if caption["start_ms"] < previous_end:
            raise RenderError("Captions must be ordered and non-overlapping")
        previous_end = caption["end_ms"]
    if "audio" in spec:
        audio = _object(spec["audio"], ("path", "in_ms", "volume_percent"))
        if Path(_path_string(audio["path"])).suffix.lower() not in (".wav", ".mp3"):
            raise RenderError("Unsupported audio container")
        _integer(audio["in_ms"], 0, MAX_DURATION_MS)
        _integer(audio["volume_percent"], 0, 100)
    return spec


@dataclass(frozen=True)
class RenderResult:
    path: Path
    sha256: str
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    fps: int
    video_codec: str
    audio_codec: str | None
    provenance: dict | None = None


def _file_sha256(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if time.monotonic() >= deadline:
                raise RenderError("Render deadline exceeded")
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: list[str], cwd: Path, timeout: float) -> bytes:
    """Drain bounded stdout, discard stderr, and kill/reap on limit or timeout."""
    if timeout <= 0:
        raise RenderError("Render deadline exceeded")
    chunks = bytearray()
    overflow = threading.Event()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH")
           if key in os.environ}
    env.update({"LC_ALL": "C", "LANG": "C", "TZ": "UTC",
                "HOME": str(cwd), "TMPDIR": str(cwd),
                "TEMP": str(cwd), "TMP": str(cwd)})
    try:
        with subprocess.Popen(args, shell=False, cwd=str(cwd), env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            def drain():
                while True:
                    chunk = process.stdout.read(4096)
                    if not chunk:
                        return
                    if len(chunks) + len(chunk) > MAX_PROBE_BYTES:
                        overflow.set()
                        process.kill()
                        return
                    chunks.extend(chunk)

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise RenderError("Render deadline exceeded") from exc
            finally:
                reader.join()
            if overflow.is_set() or process.returncode:
                raise RenderError("Tool failed or exceeded diagnostic output limit")
    except OSError as exc:
        raise RenderError("Unable to execute local media tool") from exc
    return bytes(chunks)


def _number(value):
    if type(value) not in (str, int, float):
        raise RenderError("Invalid probe number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RenderError("Invalid probe number") from exc
    if not math.isfinite(number) or number <= 0:
        raise RenderError("Invalid probe number")
    return number


class RenderEngine:
    """Trusted configuration + untrusted JSON. Media root must be privately owned.

    Tools and font are administrator configuration, never timeline fields.
    Successful MP4s remain in generated private directories under media_root.
    """

    def __init__(self, media_root: Path, *, ffmpeg: str = "ffmpeg",
                 ffprobe: str = "ffprobe", font: str | None = None,
                 timeout_seconds: int = 180):
        self.root = Path(media_root).resolve(strict=True)
        if not self.root.is_dir() or str(self.root).startswith(("\\\\", "//")):
            raise RenderError("Media root must be a local directory")
        self.timeout = _integer(timeout_seconds, 1, 600)
        self.ffmpeg = self._tool(ffmpeg)
        self.ffprobe = self._tool(ffprobe)
        self.font = self._media(font, (".ttf", ".otf")) if font is not None else None

    @staticmethod
    def _tool(value):
        resolved = shutil.which(value)
        if not resolved or Path(resolved).suffix.lower() in (".bat", ".cmd"):
            raise RenderError("Native local FFmpeg/FFprobe executable required")
        path = Path(resolved).resolve(strict=True)
        if str(path).startswith(("\\\\", "//")):
            raise RenderError("Remote executables are forbidden")
        return str(path)

    def _media(self, value, suffixes):
        relative = _path_string(value)
        try:
            path = (self.root / relative).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RenderError("Media file does not exist") from exc
        if (not path.is_relative_to(self.root) or not path.is_file()
                or path.suffix.lower() not in suffixes
                or not 0 < path.stat().st_size <= MAX_INPUT_BYTES):
            raise RenderError("Media is outside root, unsupported, or too large")
        return path

    def _probe(self, path, cwd, deadline):
        args = [self.ffprobe, "-v", "error", "-max_alloc", "67108864",
                "-protocol_whitelist", "file", "-probesize", "5000000",
                "-analyzeduration", "5000000", "-f", DEMUXERS[path.suffix.lower()],
                "-show_entries",
                "format=duration,format_name:stream=codec_type,codec_name,width,height,"
                "pix_fmt,r_frame_rate,duration,channels,sample_rate",
                "-of", "json", str(path)]
        data = _run(args, cwd, min(15, deadline - time.monotonic()))
        try:
            result = json.loads(data)
            if (type(result) is not dict or type(result.get("streams")) is not list
                    or type(result.get("format")) is not dict
                    or type(result["format"].get("format_name")) is not str
                    or any(type(s) is not dict for s in result["streams"])):
                raise RenderError("Invalid probe structure")
            _number(result["format"].get("duration"))
        except (ValueError, TypeError, RecursionError) as exc:
            raise RenderError("Invalid FFprobe metadata") from exc
        return result

    @staticmethod
    def _stream(probe, kind):
        streams = [s for s in probe["streams"] if s.get("codec_type") == kind]
        if not streams:
            raise RenderError(f"Missing {kind} stream")
        return streams[0]

    def _arguments(self, spec, paths, audio_path, cwd):
        width, height, fps = PROFILES[spec["output"]["profile"]]
        total_ms = sum(c["out_ms"] - c["in_ms"] for c in spec["clips"])
        args = [self.ffmpeg, "-nostdin", "-n", "-hide_banner", "-loglevel", "error",
                "-max_alloc", "67108864", "-cpuflags", "0",
                "-filter_threads", "1", "-filter_complex_threads", "1"]
        for path, clip in zip(paths, spec["clips"]):
            args += ["-threads", "1", "-protocol_whitelist", "file",
                     "-probesize", "5000000", "-analyzeduration", "5000000",
                     "-f", DEMUXERS[path.suffix.lower()],
                     "-ss", f'{clip["in_ms"] / 1000:.3f}',
                     "-t", f'{(clip["out_ms"] - clip["in_ms"]) / 1000:.3f}',
                     "-i", str(path)]
        if audio_path:
            args += ["-threads", "1", "-protocol_whitelist", "file",
                     "-f", DEMUXERS[audio_path.suffix.lower()],
                     "-ss", f'{spec["audio"]["in_ms"] / 1000:.3f}',
                     "-t", f"{total_ms / 1000:.3f}", "-i", str(audio_path)]
        filters = []
        for index in range(len(paths)):
            filters.append(
                f"[{index}:v:0]setpts=PTS-STARTPTS,fps={fps},"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,format=yuv420p[v{index}]")
        labels = "".join(f"[v{i}]" for i in range(len(paths)))
        filters.append(f"{labels}concat=n={len(paths)}:v=1:a=0[base]")
        overlays = ([spec["title"]] if "title" in spec else []) + spec.get("captions", [])
        current = "base"
        font_option = ""
        if overlays:
            # Configuration is trusted, but still restrict filter-language syntax.
            font_path = self.font.as_posix()
            if not re.fullmatch(r"[A-Za-z0-9_:/ .-]+", font_path):
                raise RenderError("Font location contains unsupported filter syntax")
            font_option = font_path.replace(":", r"\:")
        for index, overlay in enumerate(overlays):
            # Literal UTF-8 files, not text embedded in FFmpeg's filter language.
            text_path = cwd / f"text{index}.txt"
            text_path.write_text(overlay["text"], encoding="utf-8")
            y = "h/8" if index == 0 and "title" in spec else "h-th-h/10"
            filters.append(
                f"[{current}]drawtext=fontfile='{font_option}':textfile=text{index}.txt:"
                f"expansion=none:fontcolor=white:fontsize=24:box=1:"
                f"boxcolor=black@0.6:x=(w-tw)/2:y={y}:"
                f"enable='gte(t,{overlay['start_ms'] / 1000:.3f})*"
                f"lt(t,{overlay['end_ms'] / 1000:.3f})'[text{index}]")
            current = f"text{index}"
        if audio_path:
            filters.append(f"[{len(paths)}:a:0]asetpts=PTS-STARTPTS,"
                           f"volume={spec['audio']['volume_percent'] / 100:.2f},"
                           f"aresample=48000,apad[audio]")
        args += ["-filter_complex", ";".join(filters), "-map", f"[{current}]"]
        if audio_path:
            args += ["-map", "[audio]", "-c:a", "aac", "-b:a", "128k",
                     "-ar", "48000", "-ac", "2"]
        else:
            args += ["-an"]
        args += ["-map_metadata", "-1", "-map_chapters", "-1",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-threads", "1",
                 "-g", str(fps * 2), "-sc_threshold", "0",
                 "-fflags", "+bitexact", "-flags:v", "+bitexact",
                 "-flags:a", "+bitexact", "-movflags", "+faststart",
                 "-t", f"{total_ms / 1000:.3f}",
                 "-fs", str(MAX_OUTPUT_BYTES), "-f", "mp4", "output.mp4"]
        return args

    def render(self, payload: str) -> RenderResult:
        spec = parse_timeline(payload)
        paths = [self._media(c["path"], (".mp4", ".mov", ".mkv", ".webm"))
                 for c in spec["clips"]]
        audio_path = (self._media(spec["audio"]["path"], (".wav", ".mp3"))
                      if "audio" in spec else None)
        if ("title" in spec or spec.get("captions")) and self.font is None:
            raise RenderError("Text overlays require an administrator-configured font")
        deadline = time.monotonic() + self.timeout
        cwd = Path(tempfile.mkdtemp(prefix="render-", dir=self.root))
        # On Windows, the media root's ACL must supply equivalent private access.
        cwd.chmod(0o700)
        success = False
        try:
            inputs = list(dict.fromkeys(paths + ([audio_path] if audio_path else [])
                                        + ([self.font] if self.font else [])))
            identities = {str(path.relative_to(self.root)): _file_sha256(path, deadline)
                          for path in inputs}
            versions = {}
            for name, tool in (("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe)):
                version = _run([tool, "-version"], cwd, min(15, deadline - time.monotonic()))
                if not version.splitlines():
                    raise RenderError("Media tool did not report its version")
                versions[name] = {
                    "version": version.decode("utf-8", errors="replace").splitlines()[0],
                    "build_sha256": hashlib.sha256(version).hexdigest(),
                }
            for path, clip in zip(paths, spec["clips"]):
                probe = self._probe(path, cwd, deadline)
                stream = self._stream(probe, "video")
                width = _number(stream.get("width"))
                height = _number(stream.get("height"))
                if width > 4096 or height > 2160:
                    raise RenderError("Input resolution exceeds decoder policy")
                duration = _number(stream.get("duration", probe["format"]["duration"]))
                if clip["out_ms"] / 1000 > duration + 0.001:
                    raise RenderError("Clip trim exceeds media duration")
            if audio_path:
                probe = self._probe(audio_path, cwd, deadline)
                stream = self._stream(probe, "audio")
                if (_number(stream.get("channels")) > 8
                        or _number(stream.get("sample_rate")) > 192000
                        or spec["audio"]["in_ms"] / 1000 >=
                        _number(stream.get("duration", probe["format"]["duration"]))):
                    raise RenderError("Unsupported audio metadata or trim")
            args = self._arguments(spec, paths, audio_path, cwd)
            _run(args, cwd, deadline - time.monotonic())
            dest = cwd / "output.mp4"
            if not dest.is_file() or not 0 < dest.stat().st_size < MAX_OUTPUT_BYTES:
                raise RenderError("Output missing or exceeded byte limit")
            probe = self._probe(dest, cwd, deadline)
            video = self._stream(probe, "video")
            duration = _number(probe["format"]["duration"])
            width, height, fps = PROFILES[spec["output"]["profile"]]
            expected = sum(c["out_ms"] - c["in_ms"] for c in spec["clips"]) / 1000
            audio = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
            if (len(probe["streams"]) != 1 + bool(audio_path)
                    or "mp4" not in probe["format"].get("format_name", "").split(",")
                    or video.get("codec_name") != "h264"
                    or video.get("pix_fmt") != "yuv420p"
                    or video.get("width") != width or video.get("height") != height
                    or video.get("r_frame_rate") != f"{fps}/1"
                    or abs(duration - expected) > max(1 / fps, 0.025)
                    or (audio_path and (len(audio) != 1
                                        or audio[0].get("codec_name") != "aac"
                                        or audio[0].get("channels") != 2
                                        or audio[0].get("sample_rate") != "48000"))):
                raise RenderError("Output metadata does not match timeline profile")
            if any(_file_sha256(path, deadline) != identities[str(path.relative_to(self.root))]
                   for path in inputs):
                raise RenderError("Input changed during render")
            output_hash = _file_sha256(dest, deadline)
            canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True).encode("ascii")
            provenance = {
                "version": 1,
                "timeline_sha256": hashlib.sha256(canonical).hexdigest(),
                "inputs_sha256": identities,
                "preset": spec["output"]["profile"],
                "preset_version": 1,
                "tools": versions,
                "output_sha256": output_hash,
            }
            result = RenderResult(dest, output_hash, dest.stat().st_size,
                                  duration, width, height, fps, "h264",
                                  "aac" if audio_path else None, provenance)
            success = True
            return result
        finally:
            # Remove only our generated scratch files, never source media.
            if success:
                for scratch in cwd.iterdir():
                    if scratch.name != "output.mp4":
                        scratch.unlink()
            else:
                shutil.rmtree(cwd)
