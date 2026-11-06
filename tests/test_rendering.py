"""Renderer tests are local; real tools are optional and are never installed here."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app import rendering as r


def timeline(**changes):
    spec = {"version": 1, "clips": [
        {"path": "clip.mp4", "in_ms": 0, "out_ms": 1000}],
        "output": {"profile": "preview", "format": "mp4"}}
    spec.update(changes)
    return json.dumps(spec)


def metadata(*, output=False, audio=False, **video_changes):
    video = {"codec_type": "video", "codec_name": "h264", "width": 640,
             "height": 360, "pix_fmt": "yuv420p", "r_frame_rate": "24/1",
             "duration": "1.000"}
    video.update(video_changes)
    streams = [video]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": "aac",
                        "channels": 2, "sample_rate": "48000", "duration": "1"})
    return json.dumps({"streams": streams, "format": {
        "duration": "1.000", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}}).encode()


@pytest.fixture
def engine(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"mock input")
    # Trusted executable resolution is mocked, not the untrusted contract.
    with patch.object(r.shutil, "which", return_value=str(Path(subprocess.__file__))):
        return r.RenderEngine(tmp_path)


@pytest.mark.parametrize("payload", [
    "not json", "[]", "{}", '{"version":1,"version":1}', "[" * 2000,
    timeline(version=True), timeline(version=2),
    timeline(extra="unsupported"), timeline(clips=[]),
    timeline(clips=[{"path": "clip.mp4", "in_ms": True, "out_ms": 1000}]),
    timeline(clips=[{"path": "clip.mp4", "in_ms": 0, "out_ms": 0}]),
    timeline(clips=[{"path": "clip.mp4", "in_ms": 1, "out_ms": 1000}]),
    timeline(clips=[{"path": "clip.mp4", "in_ms": 0, "out_ms": 121000}]),
    timeline(clips=[{"path": "clip.mp4", "in_ms": 0, "out_ms": 120000}] * 2),
    timeline(clips=[{"path": "clip.mp4", "in_ms": 0, "out_ms": 1000}] * 17),
    timeline(output={"profile": "preview", "format": "webm"}),
    timeline(output={"profile": ["preview"], "format": "mp4"}),
    timeline(output={"profile": "preview", "format": "mp4", "args": ["-y"]}),
    timeline(title={"text": "bad\ntext", "start_ms": 0, "end_ms": 1000}),
    timeline(title={"text": "", "start_ms": 0, "end_ms": 1000}),
    timeline(title={"text": "ok", "start_ms": 0, "end_ms": 1001}),
    timeline(captions=[{"text": "a", "start_ms": 0, "end_ms": 800},
                       {"text": "b", "start_ms": 700, "end_ms": 1000}]),
    timeline(audio={"path": "a.wav", "in_ms": 0, "volume_percent": 101}),
    timeline(audio={"path": "a.mp4", "in_ms": 0, "volume_percent": 50}),
    timeline(audio={"path": "a.wav", "in_ms": float("nan"), "volume_percent": 50}),
])
def test_reject_invalid_timeline(payload):
    with pytest.raises(r.RenderError):
        r.parse_timeline(payload)


@pytest.mark.parametrize("path", [
    "../clip.mp4", "/tmp/clip.mp4", "C:/clip.mp4", "C:\\clip.mp4",
    "//server/share/clip.mp4", "https://example/clip.mp4", "clip.mp4:stream",
    "-i clip.mp4", "dir/../clip.mp4", "dir//clip.mp4", "clip.m3u8",
    "clip.mp4;touch x", "clip.mp4\n", "missing.mp4",
])
def test_reject_path_before_execution(engine, path):
    with patch.object(r, "_run") as run:
        with pytest.raises(r.RenderError):
            engine.render(timeline(clips=[{"path": path, "in_ms": 0, "out_ms": 1000}]))
        run.assert_not_called()


def test_reject_symlink_escape(engine, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.mp4"
    outside.write_bytes(b"outside")
    try:
        (tmp_path / "escape.mp4").symlink_to(outside)
    except OSError:
        pytest.skip("Local account cannot create symlinks")
    with pytest.raises(r.RenderError):
        engine.render(timeline(clips=[{"path": "escape.mp4", "in_ms": 0, "out_ms": 1000}]))
    assert outside.read_bytes() == b"outside"
    outside.unlink()


def test_json_byte_limit():
    with pytest.raises(r.RenderError):
        r.parse_timeline(" " * (r.MAX_JSON_BYTES + 1))


def test_missing_font_rejected_before_execution(engine):
    with patch.object(r, "_run") as run, pytest.raises(r.RenderError):
        engine.render(timeline(title={"text": "Title", "start_ms": 0, "end_ms": 1000}))
    run.assert_not_called()


def test_output_probe_checksum_and_fixed_arguments(engine):
    calls = []
    output_bytes = b"mock MP4 artifact"

    def fake_run(args, cwd, timeout):
        calls.append((args, cwd, timeout))
        if "-filter_complex" in args:
            (cwd / "output.mp4").write_bytes(output_bytes)
            return b""
        return metadata()

    with patch.object(r, "_run", side_effect=fake_run):
        result = engine.render(timeline())
    assert result.sha256 == hashlib.sha256(output_bytes).hexdigest()
    assert result.path.read_bytes() == output_bytes
    assert result.size_bytes == len(output_bytes)
    assert (result.width, result.height, result.fps, result.duration_seconds) == (640, 360, 24, 1)
    assert result.video_codec == "h264" and result.audio_codec is None
    assert len(calls) == 3
    args, cwd, timeout = calls[1]
    assert cwd.parent == engine.root and 0 < timeout <= engine.timeout
    assert args[args.index("-i") + 1] == str(engine.root / "clip.mp4")
    for option, value in [("-threads", "1"), ("-fs", str(r.MAX_OUTPUT_BYTES)),
                          ("-protocol_whitelist", "file"), ("-f", "mov"),
                          ("-map_metadata", "-1")]:
        assert args[args.index(option) + 1] == value
    assert "-nostdin" in args and "-n" in args and args[-1] == "output.mp4"
    assert list(cwd.iterdir()) == [result.path]
    assert (engine.root / "clip.mp4").read_bytes() == b"mock input"


def test_order_trim_literal_text_and_audio(engine):
    (engine.root / "second.mp4").write_bytes(b"second")
    (engine.root / "music.wav").write_bytes(b"audio")
    (engine.root / "font.ttf").write_bytes(b"font")
    engine.font = engine.root / "font.ttf"
    text = "Title: '%{metadata:x}'; [evil], \\ café"
    spec = r.parse_timeline(timeline(
        clips=[{"path": "second.mp4", "in_ms": 1000, "out_ms": 2000},
               {"path": "clip.mp4", "in_ms": 0, "out_ms": 1000}],
        title={"text": text, "start_ms": 0, "end_ms": 1000},
        captions=[{"text": "Caption", "start_ms": 1000, "end_ms": 2000}],
        audio={"path": "music.wav", "in_ms": 100, "volume_percent": 50}))
    cwd = engine.root / "scratch"
    cwd.mkdir()
    args = engine._arguments(spec, [engine.root / "second.mp4",
                                   engine.root / "clip.mp4"], engine.root / "music.wav", cwd)
    inputs = [args[i + 1] for i, value in enumerate(args) if value == "-i"]
    assert inputs == [str(engine.root / name) for name in ("second.mp4", "clip.mp4", "music.wav")]
    graph = args[args.index("-filter_complex") + 1]
    assert text not in graph
    assert (cwd / "text0.txt").read_text(encoding="utf-8") == text
    assert "expansion=none" in graph and "[v0][v1]concat=n=2" in graph
    assert "volume=0.50" in graph and "apad[audio]" in graph
    assert "1.000" in args and "0.100" in args and "2.000" in args


@pytest.mark.parametrize("probe", [
    b"invalid", b"{}", b'{"streams":[],"format":{"duration":"NaN","format_name":"mp4"}}',
    metadata(width=4097), metadata(duration="0.2"), metadata(codec_type="audio"),
])
def test_bad_input_metadata_cleans_only_scratch(engine, probe):
    with patch.object(r, "_run", return_value=probe), pytest.raises(r.RenderError):
        engine.render(timeline())
    assert list(engine.root.iterdir()) == [engine.root / "clip.mp4"]


@pytest.mark.parametrize("changes", [
    {"width": 1280}, {"codec_name": "vp9"}, {"pix_fmt": "yuv444p"},
    {"r_frame_rate": "30/1"}, {"codec_type": "audio"},
])
def test_reject_wrong_output_metadata(engine, changes):
    def fake_run(args, cwd, timeout):
        if "-filter_complex" in args:
            (cwd / "output.mp4").write_bytes(b"mock")
            return b""
        return metadata(**changes) if args[-1].endswith("output.mp4") else metadata()

    with patch.object(r, "_run", side_effect=fake_run), pytest.raises(r.RenderError):
        engine.render(timeline())
    assert list(engine.root.iterdir()) == [engine.root / "clip.mp4"]


def test_reject_truncated_duration_and_output_limit(engine):
    def fake_run(args, cwd, timeout):
        if "-filter_complex" in args:
            (cwd / "output.mp4").write_bytes(b"1234")
            return b""
        if args[-1].endswith("output.mp4"):
            value = json.loads(metadata())
            value["format"]["duration"] = "0.2"
            return json.dumps(value).encode()
        return metadata()
    with patch.object(r, "_run", side_effect=fake_run), pytest.raises(r.RenderError):
        engine.render(timeline())
    with patch.object(r, "_run", side_effect=fake_run), patch.object(r, "MAX_OUTPUT_BYTES", 4):
        with pytest.raises(r.RenderError):
            engine.render(timeline())


def test_audio_metadata_and_output_verification(engine):
    (engine.root / "audio.wav").write_bytes(b"audio")
    payload = timeline(audio={"path": "audio.wav", "in_ms": 0, "volume_percent": 80})

    def fake_run(args, cwd, timeout):
        if "-filter_complex" in args:
            (cwd / "output.mp4").write_bytes(b"audio MP4")
            return b""
        return metadata(audio=args[-1].endswith(("audio.wav", "output.mp4")))
    with patch.object(r, "_run", side_effect=fake_run):
        assert engine.render(payload).audio_codec == "aac"


def test_subprocess_policy_and_bounded_stdout(tmp_path):
    # Real local Python subprocess exercises the runner, not real media rendering.
    import sys
    with patch.object(r.subprocess, "Popen", wraps=subprocess.Popen) as popen:
        assert r._run([sys.executable, "-c", "print('ok')"], tmp_path, 5) == b"ok\n"
        assert popen.call_args.kwargs["shell"] is False
        assert popen.call_args.kwargs["cwd"] == str(tmp_path)
        assert popen.call_args.kwargs["stdin"] == subprocess.DEVNULL
        assert popen.call_args.kwargs["stderr"] == subprocess.DEVNULL
    with pytest.raises(r.RenderError, match="output limit"):
        r._run([sys.executable, "-c", "print('x' * 70000)"], tmp_path, 5)
    with pytest.raises(r.RenderError, match="deadline"):
        r._run([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, 0.1)
    with pytest.raises(r.RenderError):
        r._run([sys.executable, "-c", "raise SystemExit(1)"], tmp_path, 5)


def test_tool_and_timeout_policy(tmp_path):
    with patch.object(r.shutil, "which", return_value=None), pytest.raises(r.RenderError):
        r.RenderEngine(tmp_path)
    with patch.object(r.shutil, "which", return_value="tool.cmd"), pytest.raises(r.RenderError):
        r.RenderEngine(tmp_path)
    with pytest.raises(r.RenderError):
        r.RenderEngine(tmp_path, timeout_seconds=0)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="Local FFmpeg and FFprobe are not available")
def test_real_fixture_render_is_repeatable(tmp_path):
    ffmpeg = str(Path(shutil.which("ffmpeg")).resolve())
    r._run([ffmpeg, "-nostdin", "-n", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=640x360:rate=24", "-t", "2", "-an", "-c:v", "libx264",
            "-threads", "1", "-pix_fmt", "yuv420p", "clip.mp4"], tmp_path, 30)
    r._run([ffmpeg, "-nostdin", "-n", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000", "-t", "2", "audio.wav"], tmp_path, 30)
    engine = r.RenderEngine(tmp_path)
    payload = timeline(clips=[
        {"path": "clip.mp4", "in_ms": 1000, "out_ms": 2000},
        {"path": "clip.mp4", "in_ms": 0, "out_ms": 1000}],
        audio={"path": "audio.wav", "in_ms": 0, "volume_percent": 50})
    first = engine.render(payload)
    second = engine.render(payload)
    assert first.sha256 == second.sha256
    assert first.sha256 == hashlib.sha256(first.path.read_bytes()).hexdigest()
    assert first.duration_seconds == pytest.approx(2, abs=1 / 24)
    assert first.audio_codec == "aac" and first.size_bytes > 1000
