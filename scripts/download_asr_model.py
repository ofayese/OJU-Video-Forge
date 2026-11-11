import os
from huggingface_hub import snapshot_download

MODEL_ID = "Systran/faster-whisper-tiny"
DEST_DIR = "tests/data/models/faster-whisper-tiny"

if not os.path.exists(DEST_DIR):
    os.makedirs(DEST_DIR)

print(f"Downloading {MODEL_ID} to {DEST_DIR}...")
snapshot_download(repo_id=MODEL_ID, local_dir=DEST_DIR, local_dir_use_symlinks=False)
print("Download complete.")
