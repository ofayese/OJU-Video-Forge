import os
from pathlib import Path

import pytest


def test_local_asr_model_loading():
    model_path = Path(os.environ.get("NARRATION_ASR_MODEL", "/models/asr/faster-whisper-tiny"))
    if not model_path.is_dir():
        pytest.skip("Optional local ASR model is not provisioned")
    faster_whisper = pytest.importorskip("faster_whisper")
    engine = faster_whisper.WhisperModel(
        str(model_path), device="cpu", compute_type="int8", local_files_only=True,
    )
    assert engine is not None
