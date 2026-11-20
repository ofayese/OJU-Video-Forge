"""One-off check: verify repository-relative Markdown links in the agent docs resolve."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "AGENTS.md",
    "docs/development/AGENT-TOOL-PROFILE.md",
    "docs/development/AGENT-EXECUTION-POLICY.md",
    ".github/copilot-instructions.md",
    "docs/implementation-plan.md",
    "docs/reviews/IMPLEMENTATION-GAP-ANALYSIS.md",
    "docs/reviews/ERE-REMAINING-GAPS.md",
    "docs/reviews/ERE-FINAL-IMPLEMENTATION-REPORT.md",
]
LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")
BACKTICK_PATH = re.compile(r"`((?:app|tests|scripts|docs|services|frontend|alembic)/[^`*\s]+)`")

failures: list[str] = []
for name in FILES:
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        resolved = (path.parent / target.split("#")[0]).resolve()
        if not resolved.exists():
            failures.append(f"{name}: broken link -> {target}")
    for candidate in BACKTICK_PATH.findall(text):
        if candidate.endswith(("/", "...")):
            continue
        if candidate.startswith("alembic/versions/0001"):
            continue
        if "*" in candidate:
            continue
        if not (ROOT / candidate).exists():
            failures.append(f"{name}: missing path -> {candidate}")

for problem in failures:
    print(problem)
print("FAIL" if failures else "PASS: agent documents link and path check")
sys.exit(1 if failures else 0)
