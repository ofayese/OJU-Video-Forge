"""Synthetic, offline smoke check for the packaged CPU narration runtime."""

import argparse
import json
import tempfile
from pathlib import Path

import ctranslate2
import faster_whisper

from app import narration


def check():
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()

    options = narration.NarrationOptions(analysis_mode="transcript")
    script = [{
        "id": "synthetic", "start_ms": 0, "end_ms": 6000,
        "text": "This is a synthetic narration security test.",
    }]
    with tempfile.TemporaryDirectory(prefix="narration-security-smoke-") as work:
        directory = Path(work)
        audio = narration.synthesize(script, directory, options, 6, check)
        source = args.source
        if not source.is_file():
            raise RuntimeError(f"Synthetic input does not exist: {source}")
        source_hash = narration.digest(source)
        if source_hash.lower() != args.source_sha256.lower():
            raise RuntimeError("Synthetic input SHA-256 does not match")
        metadata = narration.probe(source, check)
        frames = narration.scenes(source, directory, metadata, options, check)
        ocr_text = next((frame["ocr"] for frame in frames
                         if "SECURITY" in (frame["ocr"] or "").upper()), None)
        if ocr_text is None:
            raise RuntimeError("Synthetic title OCR failed")
        output = directory / "derived.mp4"
        result = narration.mux(source, audio, output, metadata, options, check)
        if narration.digest(source) != source_hash:
            raise RuntimeError("Source bytes changed")
        if narration.video_hash(source, check) != narration.video_hash(output, check):
            raise RuntimeError("Source video stream changed")
        report = {
            "media": "PASS", "ocr": "PASS", "ocr_text": ocr_text, "tts": "PASS",
            "ffprobe": result, "source_visual_identity": "PASS",
            "source_sha256": source_hash,
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
