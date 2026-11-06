"""Versioned, deterministic audio and subtitle contracts for local workflows."""

from __future__ import annotations

import hashlib
import io
import json
import re
import wave


WORKFLOW_VERSION = 1
MAX_AUDIO_BYTES = 64 * 1024 * 1024
MAX_AUDIO_DURATION_MS = 2 * 60 * 60 * 1000
MAX_CAPTION_BYTES = 256 * 1024
MAX_CAPTIONS = 1000
MAX_CAPTION_TEXT = 500
_TIMESTAMP = re.compile(r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})\Z")
_TIMESTAMP_TEXT = r"\d{2,}:[0-5]\d:[0-5]\d"


class AudioWorkflowError(ValueError):
    """Invalid or unsupported audio workflow input."""


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AudioWorkflowError("Timestamp must be an integer within the supported range")
    return value


def validate_captions(captions: object) -> list[dict[str, int | str]]:
    if type(captions) is not list or len(captions) > MAX_CAPTIONS:
        raise AudioWorkflowError("Expected at most 1000 captions")
    result = []
    previous_end = 0
    for cue in captions:
        if type(cue) is not dict or set(cue) != {"start_ms", "end_ms", "text"}:
            raise AudioWorkflowError("Caption fields are invalid")
        start = _integer(cue["start_ms"], 0, MAX_AUDIO_DURATION_MS - 1)
        end = _integer(cue["end_ms"], start + 1, MAX_AUDIO_DURATION_MS)
        text = cue["text"]
        if (type(text) is not str or not 1 <= len(text) <= MAX_CAPTION_TEXT
                or any(not char.isprintable() or char in "\r\n" for char in text)):
            raise AudioWorkflowError("Caption text must be printable single-line text")
        if start < previous_end:
            raise AudioWorkflowError("Captions must be ordered and non-overlapping")
        result.append({"start_ms": start, "end_ms": end, "text": text})
        previous_end = end
    return result


def validate_audio(data: bytes) -> dict[str, int | str]:
    if type(data) is not bytes or not 44 <= len(data) <= MAX_AUDIO_BYTES:
        raise AudioWorkflowError("Audio must be a non-empty WAV file within the size limit")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels = audio.getnchannels()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            duration_ms = frames * 1000 // sample_rate
            if (audio.getcomptype() != "NONE" or channels not in (1, 2)
                    or not 8000 <= sample_rate <= 96000 or frames <= 0
                    or not 0 < duration_ms <= MAX_AUDIO_DURATION_MS):
                raise AudioWorkflowError("Unsupported WAV audio metadata")
    except (wave.Error, EOFError, ZeroDivisionError) as exc:
        raise AudioWorkflowError("Invalid PCM WAV audio") from exc
    return {"media_type": "audio/wav", "channels": channels,
            "sample_rate": sample_rate, "duration_ms": duration_ms}


def _timestamp(milliseconds: int, separator: str) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def serialize_subtitles(captions: object, format_name: str) -> bytes:
    cues = validate_captions(captions)
    if format_name not in ("srt", "vtt"):
        raise AudioWorkflowError("Subtitle format must be srt or vtt")
    blocks = []
    for index, cue in enumerate(cues, start=1):
        separator = "," if format_name == "srt" else "."
        cue_number = f"{index}\n" if format_name == "srt" else ""
        block = (f"{cue_number}{_timestamp(cue['start_ms'], separator)} --> "
                 f"{_timestamp(cue['end_ms'], separator)}\n{cue['text']}")
        blocks.append(block)
    header = "WEBVTT\n\n" if format_name == "vtt" else ""
    return (header + "\n\n".join(blocks) + ("\n" if blocks else "")).encode("utf-8")


def parse_subtitles(data: bytes, format_name: str) -> list[dict[str, int | str]]:
    if (type(data) is not bytes or len(data) > MAX_CAPTION_BYTES
            or format_name not in ("srt", "vtt")):
        raise AudioWorkflowError("Subtitle document is invalid or too large")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AudioWorkflowError("Subtitle document must be UTF-8") from exc
    if "\r" in content or "\x00" in content:
        raise AudioWorkflowError("Subtitle document contains unsupported control characters")
    if format_name == "vtt":
        if not content.startswith("WEBVTT\n\n"):
            raise AudioWorkflowError("WebVTT document must begin with WEBVTT")
        content = content[len("WEBVTT\n\n"):]
    blocks = content.rstrip("\n").split("\n\n") if content else []
    if len(blocks) > MAX_CAPTIONS:
        raise AudioWorkflowError("Expected at most 1000 captions")
    cues = []
    separator = "," if format_name == "srt" else "."
    for index, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if format_name == "srt":
            if len(lines) != 3 or lines[0] != str(index):
                raise AudioWorkflowError("SRT cue sequence or shape is invalid")
            timing, text = lines[1], lines[2]
        else:
            if len(lines) != 2:
                raise AudioWorkflowError("WebVTT cue shape is invalid")
            timing, text = lines
        match = re.fullmatch(
            rf"({_TIMESTAMP_TEXT}{re.escape(separator)}\d{{3}}) --> "
            rf"({_TIMESTAMP_TEXT}{re.escape(separator)}\d{{3}})",
            timing,
        )
        if not match:
            raise AudioWorkflowError("Subtitle timestamp is invalid")
        start, end = (_parse_timestamp(match.group(1)), _parse_timestamp(match.group(2)))
        cues.append({"start_ms": start, "end_ms": end, "text": text})
    return validate_captions(cues)


def _parse_timestamp(value: str) -> int:
    match = _TIMESTAMP.fullmatch(value)
    if not match:
        raise AudioWorkflowError("Subtitle timestamp is invalid")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(*, project_id: str, job_id: str, inputs: list[dict],
             outputs: dict[str, bytes]) -> bytes:
    document = {
        "version": WORKFLOW_VERSION,
        "project_id": project_id,
        "job_id": job_id,
        "inputs": inputs,
        "outputs": {
            name: {"sha256": checksum(data), "size_bytes": len(data)}
            for name, data in sorted(outputs.items())
        },
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
