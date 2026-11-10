# Narration image remediation evaluation

**Recommendation: BLOCKED.** Do not deploy the narration image routinely. No residual risk is accepted. Do not treat the separate `--ignore-unfixed` pass or the lower Trivy count as proof of fixes.

Assessment date: 2026-11-10. Source baseline: `179a71312f93279f6fab4b65dc08143c02eb952d`. Environment: Windows host, Linux Docker Engine 29.9.3; Trivy 0.76.2 with vulnerability DB updated 2026-11-10 19:01 UTC. No model was downloaded, no Wan inference was run, no service was deployed, and no CI policy or risk-acceptance decision was changed.

## Image comparison

| Image | Digest | Local Docker size | OS packages | HIGH / CRITICAL | Unique H/C CVEs | Affected binary packages | Strict scan |
|---|---|---:|---:|---:|---:|---:|---|
| Baseline `ovf-narration:test-security` | `sha256:99a0ab1af5e2b8785dfedb3916de0d209aafa86a3d307e10fc6af9bcd135d83b` | 1,714,054,365 bytes | 314 | 295 / 1 | 74 | 44 | Exit 1 |
| Candidate 1 `ovf-narration:minimal-ffmpeg` | `sha256:3c4565859caeade8eab4fba6e741661c7605296846f2e891d58b52bf3a853f21` | 1,161,934,373 bytes | 177 | 135 / 1 | 50 | 33 | Exit 1 |
| Candidate 2 `ovf-narration:minimal-ffmpeg-verified` | `sha256:d34d1a7e546eb8011dc355c65d1fccb6b7b459137a5bcee92001c5970dbb33c6` | 1,178,727,043 bytes | 176 | 135 / 1 | 50 | 33 | Exit 1 |

Candidate 1 failed FFprobe on the synthetic H.264/AAC source because its FFmpeg configuration disabled required demuxers. Candidate 2 corrected configure ordering and passed the integrated local smoke. The candidate 2 package diff is 138 removed, 176 unchanged, zero added, and zero version-changed packages. Candidate 2 is about 31.2% smaller than baseline.

The user-supplied earlier baseline (239 HIGH / 1 CRITICAL, 60 unique CVEs) was from an older DB. The refreshed DB raised the same baseline image to 295 HIGH / 1 CRITICAL and 74 unique CVEs; the candidate scans below use that same refreshed DB and Trivy version.

Strict scans used Trivy 0.76.2 with the refreshed DB and no `--ignore-unfixed` option:

```powershell
.\trivy_bin\trivy.exe image --skip-db-update --exit-code 1 --format json --output docs/reviews/evidence/2026-11-10-narration-remediation/trivy_baseline.json --scanners vuln --severity HIGH,CRITICAL ovf-narration:test-security
.\trivy_bin\trivy.exe image --skip-db-update --exit-code 1 --format json --output docs/reviews/evidence/2026-11-10-narration-remediation/trivy_candidate1_strict.json --scanners vuln --severity HIGH,CRITICAL ovf-narration:minimal-ffmpeg
.\trivy_bin\trivy.exe image --skip-db-update --exit-code 1 --format json --output docs/reviews/evidence/2026-11-10-narration-remediation/trivy_candidate2_strict.json --scanners vuln --severity HIGH,CRITICAL ovf-narration:minimal-ffmpeg-verified
.\trivy_bin\trivy.exe image --skip-db-update --ignore-unfixed --exit-code 1 --scanners vuln --severity HIGH,CRITICAL ovf-narration:minimal-ffmpeg-verified
```

The first three commands each exited 1 as expected. The separate candidate blocking-policy check exited 0. That pass only reflects the policy's filtering behavior; the strict candidate scan still reports 135 HIGH and 1 CRITICAL.

Sanitized full H/C scan records are [baseline JSON](./trivy_baseline_sanitized.json), [candidate 1 JSON](./trivy_candidate1_sanitized.json), and [candidate 2 JSON](./trivy_candidate2_sanitized.json). The [candidate finding inventory](./candidate2_findings.csv) retains package versions, status, descriptions, Debian data source, vendor severity scores, CVSS, and fixed-version fields. The [package diff](./package_diff_baseline_candidate2.csv) and [package-level risk register](./candidate2_risk_register.csv) are separate evidence.

## What changed in scanner results—and what did not

Candidate 2 has 24 fewer unique CVE IDs in Trivy than the refreshed baseline (74 to 50), but **no package update or CVE patch was confirmed**. Seventeen FFmpeg CVEs disappear from the package scan because the candidate statically compiles FFmpeg 7.1.5, which Trivy does not identify as a Debian package. Seven more disappear because candidate 2 no longer contains `libxi6` and `libcjson1`:

- `libxi6`: CVE-2026-109089, CVE-2026-109090.
- `libcjson1`: CVE-2026-16554, CVE-2026-29036, CVE-2026-67215, CVE-2026-67216, CVE-2026-87933.

Those are package/module removals, not patched findings. Candidate 2's 136 H/C records include 134 with Trivy status `affected`, one `fix_deferred`, and one `will_not_fix`; no H/C finding reports a fixed version. The package risk register covers 50 Trivy CVEs plus 17 separately assessed static-FFmpeg CVEs (67 unique CVEs across 16 source/build groups); repeated binary-package findings remain in the detailed inventory. The complete per-CVE package, function/precondition, module, and reachability notes for the 17 static FFmpeg findings are in [candidate2_static_ffmpeg_assessment.csv](./candidate2_static_ffmpeg_assessment.csv).

Candidate 2's FFmpeg recipe [Dockerfile.minimal-ffmpeg](../../../../services/narration/Dockerfile.minimal-ffmpeg) pins Debian builder/runtime base digests, the Debian snapshot, FFmpeg 7.1.5 archive SHA-256, and x264 commit. It removes FDK-AAC and `--enable-nonfree`, uses FFmpeg's native AAC encoder, compiles FFmpeg statically, and configures `--disable-network` with only `file` and `pipe` protocols. Runtime enumeration confirmed those protocols only. The specifically reviewed vulnerable FFmpeg components for 16 CVEs are not compiled:

`CVE-2026-58049`, `64830`, `64832`, `64833`, `64834`, `64835`, `66036`, `66039`, `66040`, `66041`, `70628`, `70632`, `75142`, `75143`, `75144`, and `75146` (all prefixed `CVE-2026-`).

This is component-level attack-surface reduction, not a software fix; shared-code safety is not established. `CVE-2026-75141` remains potentially reachable because HEVC demux/decode and MP4 muxing are enabled. PNG/APNG encoders and the `quirc` filter are absent; the enabled image output uses MJPEG. Further FFmpeg changes require repeating module and compatibility checks.

## Priority package assessment

- **Tesseract 5.5.0-1+b1:** Debian Trixie has no supported fixed package/backport identified here. Upstream Tesseract 5.5.3 fixes CVE-2026-73066; Debian Sid has a newer build, not a Trixie remediation. The vulnerable condition is untrusted `.traineddata`. The application change pins `/usr/bin/tesseract`, `/usr/share/tesseract-ocr/5/tessdata`, language `eng`, and the allowlisted traineddata SHA-256; it removes `TESSDATA_PREFIX` from the OCR subprocess environment and fails closed on a missing, symlinked, or modified asset. This blocks that model-injection route, **not** the package CVE or other Tesseract image/parser findings, which remain potentially reachable through extracted frames.
- **libxml2 2.12.7+dfsg+really2.9.14-2.1+deb13u3:** no supported Trixie fix was found. The candidate has no `python3-libxml2` package and no importable `libxml2`/`lxml` Python module; application search found no direct libxml2 or Python SAX use. This narrows the specific Python-binding CVE-2026-74860 route but does not clear the installed C library or the other seven libxml2 findings. Trivy marks CVE-2026-6653 CRITICAL using NVD severity (NVD CVSS 9.8); Debian's tracker calls the Trixie issue `<no-dsa>`/minor. Both assessments are retained.
- **FFmpeg:** Debian Trixie 7:7.1.5-0+deb13u1 has no supported fixed package identified for these findings. Debian reports CVE-2026-58049 postponed pending a 7.1.x fix; a fix is reported in upstream 9.x, not a supported Trixie backport. Candidate 2 does not upgrade the shared FFmpeg code, and the static build's scanner gap is called out above.
- **util-linux 2.41.5-0+deb13u1:** four CVEs repeat across nine Debian binary packages. No supported Trixie fixed version was identified. The application does not invoke mount/nsenter operations; the worker's UID 10001, dropped capabilities, and no-new-privileges reduce privileged-operation preconditions but do not patch the package.
- **Other H/C source packages:** all remaining findings, source/binary package mappings, installed versions, Trivy status, dependency rationale, controls, residual decision, and reassessment triggers are in the risk register. The refreshed scan adds 14 Kerberos (`krb5`) CVEs across four binary packages; no direct Kerberos API call was found, but transitive callers and individual preconditions remain unverified. `libtiff` includes a `will_not_fix` finding and `perl` a `fix_deferred` finding. Upstream patch status not established for a package/CVE is explicitly left unresolved rather than inferred from a missing Debian fixed version.

## Runtime compatibility and isolation

Candidate 2 passed FFprobe H.264/AAC probing, FFmpeg rendering, a real faster-whisper CPU-int8 transcription using the pre-existing local model, English Tesseract OCR (`SYNTHETIC SECURITY`), eSpeak synthesis, and source video preservation. The model volume was mounted read-only and a separate write probe was denied. [Smoke results](./candidate2_smoke_results.json) record the source and output hashes and the tested constraints.

The smoke container used `--network none`, `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges:true`, CPU 2, memory 2 GiB, PID limit 128, and a 512 MiB `noexec,nosuid` tmpfs. No DB credentials or Docker socket were passed/mounted. These test controls are not production isolation: the inspected live worker still has private control/inference network access and DB/Redis credential environment entries. Candidate 2 was not deployed, and no dedicated short-lived parser container was implemented. Routine deployment should remain blocked until supported fixes or an owner-approved, verified parser boundary removes those credentials/network paths and residual risks are reviewed.

The exact full temporary smoke harness command was not retained; its path could not be recovered from session history. The tests and their results are reported, but that command-level reproducibility gap remains. A previously denied Docker inspection was not retried; retained history identifies a volume-listing component but does not preserve the full request or its arguments.

## Validation and delivery

Commands executed in Docker:

| Command | Result |
|---|---|
| `docker run --rm --network none oju-video-forge-tests:local python -m pytest -q -p no:cacheprovider tests/test_narration.py -k traineddata` | 2 passed |
| `docker run --rm --network none oju-video-forge-tests:local python -m pytest -q -p no:cacheprovider tests/test_narration.py` | 24 passed, 1 warning |
| `docker run --rm --network none oju-video-forge-tests:local ruff check --no-cache app/narration.py tests/test_narration.py` | Passed |
| `docker run --rm --network none oju-video-forge-tests:local python -m pytest -q -p no:cacheprovider --ignore=tests/test_model_loading.py` | 277 passed, 12 skipped, 5 warnings; optional-ASR test excluded because `faster_whisper` is absent from the general test image |

The candidate was built as an evaluation image only; no deployment or merge was performed. No CI policy was weakened. No residual risk was accepted. **Recommendation remains BLOCKED** for routine narration-image deployment pending supported package fixes or a separately validated parser-isolation/remediation plan.
