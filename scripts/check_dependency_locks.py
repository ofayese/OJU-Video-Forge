from __future__ import annotations

import subprocess
import sys
import tempfile
from difflib import unified_diff
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "piptools",
                    "compile",
                    "--quiet",
                    "--generate-hashes",
                    "--allow-unsafe",
                    "--constraint",
                    str(committed),
                    "--output-file",
                    str(generated),
                    str(source),
                ],
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
