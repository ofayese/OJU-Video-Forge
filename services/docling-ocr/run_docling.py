"""Launch the pinned Docling Serve CLI without an intermediate child process."""

import argparse
import os
import shutil


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local Docling OCR service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9623)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    executable = shutil.which("docling-serve")
    if executable is None:
        raise RuntimeError("pinned docling-serve executable is unavailable")
    os.execv(executable, [
        executable, "run", "--host", args.host, "--port", str(args.port),
    ])


if __name__ == "__main__":
    main()
