import os
from pathlib import Path
try:
    from faster_whisper import WhisperModel
except ImportError as e:
    print(f"ImportError: {e}")
    exit(1)

model_path = os.environ.get("NARRATION_ASR_MODEL", "/models/asr/faster-whisper-tiny")
print(f"Testing model loading from: {model_path}")

if not Path(model_path).is_dir():
    print(f"Error: {model_path} is not a directory")
    exit(1)

try:
    engine = WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)
    print("Success: WhisperModel loaded successfully!")
except Exception as e:
    print(f"Error loading WhisperModel: {e}")
    exit(1)
