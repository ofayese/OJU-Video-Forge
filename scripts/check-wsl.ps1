$ErrorActionPreference = 'Stop'
Write-Verbose 'Windows NVIDIA driver:'
nvidia-smi
Write-Verbose 'WSL status:'
wsl --status
Write-Verbose 'Docker GPU smoke test (from WSL shell):'
Write-Verbose 'docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi'
