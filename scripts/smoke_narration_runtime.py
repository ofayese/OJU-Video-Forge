"""Synthetic, offline smoke check for the packaged CPU narration runtime."""

import json
import tempfile
from pathlib import Path

import ctranslate2
import faster_whisper

from app import narration


def check():
    pass


def main():
    options = narration.NarrationOptions(analysis_mode="transcript")
    script = [{
        "id": "synthetic", "start_ms": 0, "end_ms": 6000,
        "text": "This is a synthetic narration security test.",
    }]
    with tempfile.TemporaryDirectory(prefix="narration-security-smoke-") as work:
        directory = Path(work)
        audio = narration.synthesize(script, directory, options, 6, check)
        source = directory / "source.mp4"
        narration.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=c=blue:s=320x180:r=12", "-i", str(audio),
            "-vf", "drawtext=fontfile=/usr/share/fonts/dejavu/DejaVuSans.ttf:"
            "text='SYNTHETIC':fontcolor=white:fontsize=24:x=20:y=20",
            "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-threads", "2", str(source),
        ], check)
        source_hash = narration.digest(source)
        metadata = narration.probe(source, check)
        frames = narration.scenes(source, directory, metadata, options, check)
        if not any("SYNTHETIC" in (frame["ocr"] or "") for frame in frames):
            raise RuntimeError("Synthetic title OCR failed")
        output = directory / "derived.mp4"
        result = narration.mux(source, audio, output, metadata, options, check)
        if narration.digest(source) != source_hash:
            raise RuntimeError("Source bytes changed")
        if narration.video_hash(source, check) != narration.video_hash(output, check):
            raise RuntimeError("Source video stream changed")
        report = {
            "media": "PASS", "ocr": "PASS", "tts": "PASS",
            "ffprobe": result, "source_visual_identity": "PASS",
            "output_bytes": output.stat().st_size,
            "output_sha256": narration.digest(output),
            "faster_whisper": faster_whisper.__version__,
            "ctranslate2": ctranslate2.__version__,
            "cpu_compute_types": sorted(ctranslate2.get_supported_compute_types("cpu")),
        }
        try:
            transcript = narration.transcribe(source, directory, metadata, check)
        except narration.NarrationError as exc:
            report["asr"] = {"status": "NOT_VERIFIED", "error": str(exc)}
            print(json.dumps(report, sort_keys=True))
            return 1
        text = " ".join(segment["text"] for segment in transcript["segments"])
        if "security" not in text.lower():
            raise RuntimeError("Synthetic speech was not recognized")
        if any(not 0 <= segment["start_ms"] < segment["end_ms"] <= 6150
               for segment in transcript["segments"]):
            raise RuntimeError("Transcription timestamps exceed the synthetic clip")
        report["asr"] = {"status": "PASS", "language": transcript["language"],
                         "segments": transcript["segments"],
                         "provider": transcript.get("provider")}
        print(json.dumps(report, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
