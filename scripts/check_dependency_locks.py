from __future__ import annotations

import subprocess
import sys
import tempfile
from difflib import unified_diff
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# pip, setuptools, wheel and related packaging tools are provided by the base
# image and deliberately excluded from lock files. Pinning them breaks
# `pip install --require-hashes` and reintroduces CVE-2025-47273. pip-compile
# excludes them by default, so --allow-unsafe is not passed (it would force
# their inclusion).
# NOTE: pip-compile excludes pip from its output even when pip is a direct
# dependency (e.g. required by pip-api via pip-audit). Such packages must be
# manually added to the lock file for --require-hashes installs, and excluded
# from the lock comparison below.
COMPARISON_EXCLUSIONS = {
    "requirements-dev.lock.txt": ("pip",),
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
                "--constraint",
                str(committed),
            ]
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
                # Filter out packages that pip-compile excludes from output but
                # are manually present in the committed lock file (e.g. pip for
                # --require-hashes compatibility). Removes the package line plus
                # all its hash/via lines.
                exclusions = COMPARISON_EXCLUSIONS.get(committed.name, ())

                def _filter(lines: list[str]) -> list[str]:
                    result = []
                    skipping = False
                    for line in lines:
                        if skipping:
                            # Still inside excluded package block: skip hash/via lines
                            if line and not line.startswith("--hash=") and not line.lstrip().startswith("#"):
                                skipping = False
                            else:
                                continue
                        if any(line.startswith(pkg + "==") for pkg in exclusions):
                            skipping = True
                            continue
                        result.append(line)
                    return result

                committed_filtered = _filter(_lock_content(committed))
                generated_filtered = _filter(_lock_content(generated))
                if committed_filtered != generated_filtered:
                    print(
                        f"ERROR: {committed.name} is stale; regenerate it with pip-compile",
                        file=sys.stderr,
                    )
                    diff = unified_diff(
                        committed_filtered,
                        generated_filtered,
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
