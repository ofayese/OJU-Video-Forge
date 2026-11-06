import hashlib
import json
import wave
from io import BytesIO

import pytest

from app.audio_workflows import (
    AudioWorkflowError,
    manifest,
    parse_subtitles,
    serialize_subtitles,
    validate_audio,
    validate_captions,
)


def synthetic_wav():
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


def test_synthetic_audio_is_validated():
    metadata = validate_audio(synthetic_wav())
    assert metadata == {
        "media_type": "audio/wav",
        "channels": 1,
        "sample_rate": 16000,
        "duration_ms": 10,
    }


@pytest.mark.parametrize("data", [b"", b"not wav", synthetic_wav()[:20]])
def test_invalid_audio_is_rejected(data):
    with pytest.raises(AudioWorkflowError):
        validate_audio(data)


def test_caption_timing_and_text_are_strict():
    assert validate_captions([
        {"start_ms": 0, "end_ms": 1000, "text": "first"},
        {"start_ms": 1000, "end_ms": 2000, "text": "second"},
    ])[1]["start_ms"] == 1000
    for captions in (
        [{"start_ms": True, "end_ms": 1, "text": "x"}],
        [{"start_ms": 1, "end_ms": 1, "text": "x"}],
        [{"start_ms": 0, "end_ms": 2, "text": "line\nbreak"}],
        [{"start_ms": 10, "end_ms": 20, "text": "later"},
         {"start_ms": 9, "end_ms": 21, "text": "overlap"}],
    ):
        with pytest.raises(AudioWorkflowError):
            validate_captions(captions)


@pytest.mark.parametrize("format_name", ["srt", "vtt"])
def test_subtitle_round_trip_is_deterministic(format_name):
    captions = [
        {"start_ms": 0, "end_ms": 1234, "text": "Hello"},
        {"start_ms": 2500, "end_ms": 4000, "text": "世界"},
    ]
    document = serialize_subtitles(captions, format_name)
    assert parse_subtitles(document, format_name) == captions
    assert serialize_subtitles(parse_subtitles(document, format_name), format_name) == document


@pytest.mark.parametrize("format_name,data", [
    ("srt", b"2\n00:00:00,000 --> 00:00:01,000\nmissing cue one"),
    ("srt", b"1\n00:00:02,000 --> 00:00:01,000\nbad time"),
    ("vtt", b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\none\nsecond"),
    ("vtt", b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nline\nbreak"),
])
def test_malformed_subtitles_are_rejected(format_name, data):
    with pytest.raises(AudioWorkflowError):
        parse_subtitles(data, format_name)


def test_manifest_records_project_job_and_content_checksums():
    source = b"synthetic audio"
    subtitle = b"synthetic captions"
    result = json.loads(manifest(
        project_id="project-id",
        job_id="job-id",
        inputs=[{"asset_id": "audio-id", "sha256": hashlib.sha256(source).hexdigest()}],
        outputs={"captions.srt": subtitle},
    ))
    assert result["version"] == 1
    assert result["project_id"] == "project-id"
    assert result["outputs"]["captions.srt"] == {
        "sha256": hashlib.sha256(subtitle).hexdigest(),
        "size_bytes": len(subtitle),
    }
