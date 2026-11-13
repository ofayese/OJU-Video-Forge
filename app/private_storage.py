"""Private local storage helpers for generated, server-named media keys."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

_KEY = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*\Z")


def _key_path(root: Path, key: str) -> tuple[Path, Path]:
    if (type(key) is not str or len(key) > 512 or not _KEY.fullmatch(key)
            or any(part in ("", ".", "..") for part in key.split("/"))):
        raise ValueError("Invalid private storage key")
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError("Private storage root must be a directory")
    if os.name != "nt":
        resolved_root.chmod(0o700)
    path = resolved_root.joinpath(*key.split("/"))
    return resolved_root, path


def write_private_file(root: Path, key: str, data: bytes) -> None:
    if type(data) is not bytes:
        raise ValueError("Private storage accepts bytes only")
    resolved_root, path = _key_path(root, key)
    parent = resolved_root
    for part in key.split("/")[:-1]:
        parent = parent / part
        parent.mkdir(mode=0o700, exist_ok=True)
        resolved_parent = parent.resolve(strict=True)
        if resolved_parent != parent or not resolved_parent.is_relative_to(resolved_root):
            raise ValueError("Storage key escapes private root")
        if os.name != "nt":
            resolved_parent.chmod(0o700)
    if path.exists():
        raise ValueError("Private storage destination is invalid or already exists")
    temporary = parent / f".tmp-{uuid.uuid4().hex}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def private_file(root: Path, key: str) -> Path:
    resolved_root, path = _key_path(root, key)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileNotFoundError("Private media is unavailable") from exc
    if (resolved != path or not resolved.is_file()
            or not resolved.is_relative_to(resolved_root)):
        raise FileNotFoundError("Private media is unavailable")
    return resolved
