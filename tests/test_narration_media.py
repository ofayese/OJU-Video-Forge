import shutil
import subprocess

import pytest

from app import narration

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Local FFmpeg tools are required for media-stream identity tests",
)


def _h264_encoder():
    """Return an H.264 encoder available on this ffmpeg build."""
    result = subprocess.run(
        ["ffmpeg", "-encoders"], capture_output=True, text=True, check=True
    )
    for name in ("libx264", "libopenh264", "h264_nvenc", "h264_amf", "h264_mf"):
        if f" {name} " in result.stdout or f" {name}\n" in result.stdout:
            return name
    raise RuntimeError("No H.264 encoder available")


@pytest.mark.parametrize("mode", ["replace", "mix", "preserve"])
def test_mux_preserves_visuals_duration_and_deliberate_audio_mode(tmp_path, mode):
    source = tmp_path / "source.mp4"
    audio = tmp_path / "narration.wav"
    def check():
        return None
    narration.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                   "color=c=blue:s=160x90:r=12", "-f", "lavfi", "-i", "sine=frequency=440",
                   "-t", "1", "-c:v", _h264_encoder(), "-threads", "1", "-c:a", "aac",
                   str(source)], check)
    narration.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                   "sine=frequency=880", "-t", "1", str(audio)], check)
    source_sha = narration.digest(source)
    output = tmp_path / "derived.mp4"
    result = narration.mux(source, None if mode == "preserve" else audio, output,
                           narration.probe(source, check),
                           narration.NarrationOptions(audio_mode=mode), check)
    assert float(result["format"]["duration"]) == 1
    assert narration.video_hash(source, check) == narration.video_hash(output, check)
    assert narration.digest(source) == source_sha
    if mode == "preserve":
        assert narration.audio_hash(source, check) == narration.audio_hash(output, check)
    else:
        assert narration.audio_hash(source, check) != narration.audio_hash(output, check)
