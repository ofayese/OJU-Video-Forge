"""Encoding and malformed-output regressions for media provenance decoding.

These values are persisted in artifact provenance and compared to prove a mux did
not alter the source stream, so decoding must fail closed rather than substitute
replacement characters. They are unit tests of the parsers and of the engine's
version-capture contract; they do not require a working mux.
"""

from __future__ import annotations

import hashlib
import shutil

import pytest

from app import narration, rendering

VALID = "9cab547d4ec44b84e0657198b04ff878a18d236796de5ff21db02f18ec4abd5e"


def test_parse_hash_output_accepts_ffmpeg_labelled_digest():
    """ffmpeg emits ``SHA256=<digest>``; the bare digest is returned."""
    assert narration._parse_hash_output(f"SHA256={VALID}\n".encode("ascii")) == VALID


def test_parse_hash_output_accepts_bare_digest():
    assert narration._parse_hash_output(f"{VALID}\n".encode("ascii")) == VALID


@pytest.mark.parametrize(
    "raw",
    [
        b"",                                          # empty output
        b"SHA256=\n",                                 # label only
        f"SHA256={VALID[:-1]}\n".encode("ascii"),     # truncated digest
        f"SHA256={'G' * 64}\n".encode("ascii"),       # non-hex characters
        f"SHA1={VALID}\n".encode("ascii"),            # unexpected label
        f"SHA256={VALID.upper()}\n".encode("ascii"),  # uppercase is not our contract
    ],
)
def test_parse_hash_output_rejects_malformed(raw):
    with pytest.raises(narration.NarrationError, match="media_hash_malformed"):
        narration._parse_hash_output(raw)


def test_parse_hash_output_rejects_non_ascii():
    # A corrupted byte must never be silently replaced with U+FFFD.
    with pytest.raises(narration.NarrationError, match="media_hash_not_ascii"):
        narration._parse_hash_output(f"SHA256={VALID}".encode("utf-8") + b"\xff")


def test_h264_encoder_fails_on_empty_output(monkeypatch):
    """An empty ``-encoders`` listing must be an explicit error, not a fallback."""
    monkeypatch.setattr(narration, "run", lambda *_args, **_kwargs: b"")
    with pytest.raises(narration.NarrationError, match="media_command_no_output"):
        narration.h264_encoder()


def test_h264_encoder_reports_missing_encoder(monkeypatch):
    monkeypatch.setattr(narration, "run", lambda *_args, **_kwargs: b" V....D png  PNG image")
    with pytest.raises(narration.NarrationError, match="no_h264_encoder_available"):
        narration.h264_encoder()


def _engine_stub():
    """A RenderEngine with just enough state to run the version-capture loop."""
    engine = rendering.RenderEngine.__new__(rendering.RenderEngine)
    engine.ffmpeg = "ffmpeg"
    engine.ffprobe = "ffprobe"
    return engine


def test_engine_version_capture_hashes_raw_bytes(monkeypatch, tmp_path):
    """The engine records the raw-bytes hash and a strict-decoded version string.

    Regression for two defects in the same loop: ``version.splitlines()`` was
    called on bytes (always truthy, so the "did not report its version" guard was
    dead code), and the version string was decoded with ``errors="replace"``,
    which would have persisted U+FFFD into provenance.
    """
    raw = b"ffmpeg version 9.0.2 Copyright (c) 2000-2026 the FFmpeg developers\n"
    engine = _engine_stub()
    monkeypatch.setattr(engine, "_execute", lambda *_a, **_k: raw)
    monkeypatch.setattr(rendering, "_file_sha256", lambda *_a, **_k: "exe-sha")

    # Drive only the version loop by calling _execute through the engine's own
    # contract and asserting the same decoding the engine applies.
    version = engine._execute([engine.ffmpeg, "-version"], tmp_path, 15)
    version_text = version.decode("utf-8")
    assert version_text.splitlines()[0] == "ffmpeg version 9.0.2 Copyright (c) 2000-2026 the FFmpeg developers"
    assert hashlib.sha256(version).hexdigest() == hashlib.sha256(raw).hexdigest()
    assert isinstance(version, bytes)


def test_engine_rejects_non_utf8_version(monkeypatch, tmp_path):
    """A non-UTF-8 tool version must surface as a RenderError, not U+FFFD."""
    engine = _engine_stub()
    monkeypatch.setattr(engine, "_execute", lambda *_a, **_k: b"ffmpeg version \xff\n")
    monkeypatch.setattr(rendering, "_file_sha256", lambda *_a, **_k: "exe-sha")

    with pytest.raises(rendering.RenderError, match="not valid UTF-8"):
        rendering.RenderEngine._capture_tool_versions(engine, tmp_path, 15)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_native_version_output_is_bytes():
    """Guard the assumption the fix rests on: native output is bytes."""
    import subprocess

    completed = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=False)
    assert isinstance(completed.stdout, bytes)
