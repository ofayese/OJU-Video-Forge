from __future__ import annotations

import subprocess
import sys
import tempfile
from difflib import unified_diff
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# pip and setuptools are packaging tools the base image already provides (and the
# runtime images deliberately delete setuptools). Pinning them in the locks breaks
# `pip install --require-hashes` (pip uninstalls setuptools mid-transaction and its
# self-upgrade to pip 26 makes `pip check` fail on the adjacent setuptools metadata)
# and reintroduces CVE-2025-47273; the layers are also never scanned before the
# unsafe copy lands, so the pins cannot be remediated in-image.
UNSAFE_EXCLUSIONS = {
    "requirements.lock.txt": ("pip", "setuptools"),
    "requirements-narration.lock.txt": ("pip", "setuptools"),
    "requirements-dev.lock.txt": (
        "pip",
        "setuptools",
        "wheel",
        "autocommand",
        "backports.tarfile",
        "importlib-metadata",
        "importlib-resources",
        "inflect",
        "jaraco.context",
        "jaraco.functools",
        "jaraco.text",
        "more-itertools",
        "typeguard",
        "zipp",
    ),
}
LOCKS = (
    (ROOT / "requirements.txt", ROOT / "requirements.lock.txt"),
    (ROOT / "requirements-dev.txt", ROOT / "requirements-dev.lock.txt"),
    (ROOT / "requirements-narration.txt", ROOT / "requirements-narration.lock.txt"),
)


def _lock_content(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ovf-lock-check-") as temp_dir:
        for source, committed in LOCKS:
            generated = Path(temp_dir) / committed.name
            command = [
                sys.executable,
                "-m",
                "piptools",
                "compile",
                "--quiet",
                "--generate-hashes",
                "--allow-unsafe",
                "--constraint",
                str(committed),
            ]
            for package in UNSAFE_EXCLUSIONS.get(committed.name, ()):
                command += ["--unsafe-package", package]
            result = subprocess.run(
                command + ["--output-file", str(generated), str(source)],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            if result.returncode:
                print(
                    f"ERROR: pip-compile failed for {source.name} "
                    f"(exit {result.returncode})",
                    file=sys.stderr,
                )
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                return result.returncode
            if _lock_content(generated) != _lock_content(committed):
                print(
                    f"ERROR: {committed.name} is stale; regenerate it with pip-compile",
                    file=sys.stderr,
                )
                diff = unified_diff(
                    _lock_content(committed),
                    _lock_content(generated),
                    fromfile=committed.name,
                    tofile="generated",
                    lineterm="",
                )
                for index, line in enumerate(diff):
                    if index == 40:
                        print("... diff truncated ...", file=sys.stderr)
                        break
                    print(line, file=sys.stderr)
                return 1
    print("PASS: committed Python locks match their manifests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
