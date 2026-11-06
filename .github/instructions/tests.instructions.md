---
name: Test and Validation Standards
description: "Use when adding or changing tests, validation scripts, acceptance gates, or CI workflows."
applyTo: "tests/**, frontend/**/*.test.ts, frontend/**/*.test.tsx, scripts/**, .github/workflows/**"
---

# Test guidance

- Add focused tests for new behavior and regressions. Tests should fail against the
  relevant defect and assert observable behavior.
- Do not skip, weaken, or broadly mock a security or acceptance check to obtain a
  passing result. Keep optional tool-dependent integration skips explicit.
- Keep unit tests independent of ERE services, Azure, AD, GPU, and public endpoints.
  Use isolated service containers for tests that require PostgreSQL or Redis.
- Validate the actual generated API schema, migration compatibility, dependency
  resolution, and media outputs rather than proxies where required.
- Report the exact command, interpreter/runtime, exit code, pass/fail/skip counts,
  warnings, and unverified gates. A local pass is not a GitHub Actions pass.
