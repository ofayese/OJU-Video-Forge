"""Fail closed unless the pinned Docling/RapidOCR stack can select CUDA."""

import importlib.metadata
import json
import shutil
from pathlib import Path

import onnxruntime
import torch
from docling.utils.accelerator_utils import decide_device
from run_docling import main


def verify_gpu_runtime() -> None:
    versions = {
        # Keep aligned with DOCLING_SERVE_VERSION in app/narration.py.
        "docling-serve": "1.38.2",
        "docling-slim": "2.142.1",
        "rapidocr": "3.10.0",
        "onnxruntime-gpu": "1.31.0",
        "torch": "2.14.1+cu130",
        "torchvision": "0.29.1+cu130",
    }
    actual = {name: importlib.metadata.version(name) for name in versions}
    if actual != versions:
        raise RuntimeError(f"gpu_dependency_mismatch:{actual}")
    providers = onnxruntime.get_available_providers()
    if "CUDAExecutionProvider" not in providers:
        raise RuntimeError(f"cuda_execution_provider_unavailable:{providers}")
    if torch.version.cuda != "13.0" or not torch.cuda.is_available():
        raise RuntimeError("torch_cuda_runtime_unavailable")
    device = decide_device("auto")
    if not device.startswith("cuda:"):
        raise RuntimeError(f"docling_selected_non_cuda_device:{device}")
    model_directory = Path("/opt/app-root/src/.cache/docling/models/RapidOcr")
    model_names = (
        "PP-OCRv6_det_small.onnx",
        "PP-OCRv6_rec_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    )
    models = {
        name: (model_directory / name).stat().st_size
        for name in model_names
        if (model_directory / name).is_file()
    }
    if len(models) != len(model_names) or any(size <= 0 for size in models.values()):
        raise RuntimeError("approved_rapidocr_model_assets_unavailable")
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        raise RuntimeError("nvidia_container_utility_unavailable")
    print(json.dumps({
        "status": "CUDA_READY",
        "device": torch.cuda.get_device_name(0),
        "torch_cuda": torch.version.cuda,
        "docling_device": device,
        "onnxruntime_providers": providers,
        "dependencies": actual,
        "rapidocr_model_bytes": models,
        "no_model_download": True,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    verify_gpu_runtime()
    main()
