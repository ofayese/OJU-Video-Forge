import shutil

import pytest

from app import narration

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Local FFmpeg tools are required for media-stream identity tests",
)


@pytest.mark.parametrize("mode", ["replace", "mix", "preserve"])
def test_mux_preserves_visuals_duration_and_deliberate_audio_mode(tmp_path, mode):
    source = tmp_path / "source.mp4"
    audio = tmp_path / "narration.wav"
    def check():
        return None
    narration.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                   "color=c=blue:s=160x90:r=12", "-f", "lavfi", "-i", "sine=frequency=440",
                   "-t", "1", "-c:v", "libx264", "-threads", "1", "-c:a", "aac",
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
