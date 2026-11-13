"""Exercise the local video-frame OCR path against the Docling Serve sidecar."""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import narration


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="docling-ocr-smoke-") as work:
        directory = Path(work)
        source = directory / "synthetic.mp4"
        narration.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=c=white:s=1280x720:r=2:d=1", "-vf",
            "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            "text=SYNTHETIC-OCR-TEST:fontcolor=black:fontsize=52:"
            "x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", "-y", str(source),
        ], lambda: None, timeout=30)
        metadata = narration.probe(source, lambda: None)
        frames = narration.scenes(
            source, directory, metadata,
            narration.NarrationOptions(analysis_mode="manual"), lambda: None,
        )
        if not frames:
            raise RuntimeError("docling_ocr_smoke_no_frames")
        text = " ".join(frame["ocr"] or "" for frame in frames).upper()
        if not all(word in text for word in ("SYNTHETIC", "OCR", "TEST")):
            raise RuntimeError(f"docling_ocr_smoke_text_mismatch:{text}")
        if (frames[0]["id"] != "frame-0" or frames[0]["timestamp_ms"] != 0
                or frames[0]["ocr_status"] != "complete"):
            raise RuntimeError("docling_ocr_smoke_frame_provenance_mismatch")
        provenance = frames[0]["ocr_provenance"]
        if provenance["provider"] != "docling-serve" or provenance["engine"] != "rapidocr":
            raise RuntimeError("docling_ocr_smoke_unexpected_provenance")
        print(json.dumps({
            "status": "PASS",
            "timestamp_ms": frames[0]["timestamp_ms"],
            "text": text.strip(),
            "provenance": provenance,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
