# Narration image security remediation - 2026-11-09

## Disposition: BLOCKED on supported-distribution security fixes

Base commit: `dc8fa7adc8df54dac179038b2e513f0e3c91893c`.
Only the optional narration image's zlib installation is changed.
No application behavior, Python locks, other images, CI policy, model assets,
canonical services, database or private recording was changed.

| HIGH/CRITICAL measurement | Before | After |
|---|---:|---:|
| HIGH package/CVE findings | 241 | 239 |
| CRITICAL package/CVE findings | 1 | 1 |
| Total package/CVE findings | 242 | 240 |
| Unique CVEs | 62 | 60 |
| Distinct Debian source packages | 18 | 17 |
| Trivy policy exit code | 1 | 1 |

This is a supported partial fix, **not** a resolved security gate or accepted
residual risk. No ignore file, VEX exception, severity change, scanner disabling,
package-database deletion or distro/version relabeling was used.

## Original evidence and grouping

The failed step was
[Scan optional narration image for vulnerabilities](https://github.com/ofayese/OJU-Video-Forge/actions/runs/42677288286/job/129361973163).
It reported Debian 13.7. The workflow uploaded only a dependency SBOM, not a
Trivy JSON report. The original narration scan table was retained locally and
converted to structured package/CVE records; that conversion is explicitly not
represented as an original Trivy JSON export.

All 242 table findings were reconciled against the official
[Debian tracker JSON](https://security-tracker.debian.org/tracker/data/json).
The rebuilt image was scanned once with Trivy 0.76.2 and a real JSON export was
retained locally. Its SHA-256 is
`4e58053f71e659e9ec7c17cbf984b0d63405edeeb005198184aa935467357601`.
The 240 remaining package/CVE identities exactly match the original findings
minus the two zlib CVEs; there were no new HIGH/CRITICAL findings.
Secret scanning also remained enabled and reported zero secrets.

Repeated binary-package findings must not be presented as distinct CVEs:

- FFmpeg: 17 CVEs repeated across `ffmpeg`, `libavcodec61`, `libavdevice61`,
  `libavfilter10`, `libavformat61`, `libavutil59`, `libpostproc58`,
  `libswresample5`, `libswscale8`: **153 findings**.
- util-linux: four CVEs repeated across `bsdutils`, `libblkid1`, `liblastlog2-2`,
  `libmount1`, `libsmartcols1`, `libuuid1`, `login`, `mount`, `util-linux`:
  **36 findings**.
- Tesseract: six CVEs repeated across `libtesseract5` and `tesseract-ocr`:
  **12 findings**.
- ncurses: one CVE across four binary packages: **four findings**.
- systemd and libX11 likewise have repeated source-package CVEs.

## Supported correction and backport verification

The pinned base contained `zlib1g` `1:1.3.dfsg+really1.3.1-1+b1`.
Installing other packages did not require APT to upgrade that already-satisfied
dependency. Explicitly install the patched security revision
`zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1` in the existing APT layer.
The rebuilt image's dpkg metadata confirmed that exact version.

[DSA-6613-1](https://security-tracker.debian.org/tracker/DSA-6613-1) fixes
[CVE-2026-76844](https://security-tracker.debian.org/tracker/CVE-2026-76844)
and [CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091)
in that Trixie revision. The security fix is backported; an upstream-looking
version of 1.3.1 does not mean the Debian security revision is unpatched.
Both findings disappeared from the unchanged-policy rescan.

All remaining CVEs below were `open` for Trixie in Debian's tracker, without a
Trixie fixed version. For example,
[CVE-2026-58049](https://security-tracker.debian.org/tracker/CVE-2026-58049)
is deferred pending a fix in FFmpeg's 7.1.x branch; fixes exist in the newer
upstream/unstable 9.x line, not as a supported Trixie package.
The distinction is **no supported Trixie fix**, not a claim that no upstream
commit exists anywhere.

## Package necessity, exposure and residual risk

The following inventory is of the built container, not the ERE host. Actual
dpkg `Depends` closures and ELF linkage of FFmpeg, FFprobe, Tesseract, eSpeak NG
and Python were inspected. "Required by" below means a distro dependency
closure, not proof that the vulnerable routine executes on every input.

Mitigations used in this review:

- **M1 - containment, not remediation:** the smoke ran as UID/GID 10001, with
  all capabilities dropped, no-new-privileges, read-only root, bounded tmpfs,
  CPU/memory/PID limits, no network and a read-only existing model volume.
  Compose's existing private-network/non-root controls are unchanged; their
  presence in configuration is not a fresh canonical deployment attestation.
- **M2 - limited interface use:** only fixed local-media command arrays were
  exercised; no display server, privileged mount operation or arbitrary shell
  input was used. This does not establish that all vulnerable codec/parser
  paths are unreachable for untrusted uploaded files.
- **M3 - feature-specific qualification:** the image has no importable libxml2
  Python bindings. The Python-SAX-only CVE has reduced applicable exposure,
  while the C library remains linked into media/OCR dependencies. No finding
  was suppressed or declared a scanner false positive.
- **M4 - retained base compatibility:** do not force-remove Essential packages,
  break Python stdlib extensions or remove shared libraries still required by
  linked executables to make an inventory look clean. Unused CLI entry points
  are not equivalent to unused source-package libraries.

Every residual row remains an unresolved HIGH finding, except libxml2 also
contains the one CRITICAL finding. M1/M2 reduce attack opportunities, not the
scan result or the need for a fix.

| Debian source / representative installed version | Findings | Required by / runtime exposure | Mitigation and residual risk |
|---|---:|---|---|
| util-linux `2.41.5-0+deb13u1` | 36 | Library dependencies in FFmpeg/Tesseract closures; mount/login/nsenter workflows are not application operations | M1, M2, M4; privileged-operation preconditions reduced, all four CVEs retained |
| ffmpeg `7:7.1.5-0+deb13u1` | 153 | Required media decoding, probing, filtering and muxing | M1, M2; crafted-media parser/memory-corruption risk remains |
| acl `2.3.2-2+b1` | 1 | Tesseract dependency closure | M1, M2; no reachability waiver |
| cjson `1.7.18-3.1+deb13u1` | 5 | FFmpeg dependency closure | M1, M2; parser/library risk remains |
| curl `8.14.1-2+deb13u7` | 2 | Tesseract dependency closure | M1, M2; not exposed as a new HTTP service, no library waiver |
| expat `2.9.0-1~deb13u1` | 4 | FFmpeg/Tesseract dependency closures | M1, M2; XML-parser risk remains |
| ncurses `6.5+20250216-2` | 4 | FFmpeg dependency closure and base terminal support | M1, M4; no terminal use required by smoke, no package waiver |
| libsndfile `1.2.2-2+deb13u1` | 1 | FFmpeg/eSpeak NG dependency closures | M1, M2; audio-parser risk remains |
| sqlite3 `3.46.1-7+deb13u2` | 3 | Base Python stdlib compatibility; no smoke/canonical database accessed | M1, M4; not a new database service, inventory findings remain |
| systemd `257.13-1~deb13u1` | 2 | `libsystemd0`/`libudev1` in FFmpeg/eSpeak NG closures, not a daemon started by the worker | M1, M2; shared-library findings retained |
| tesseract `5.5.0-1+b1` | 12 | Required local OCR binary and library | M1, M2; crafted-image/parser risk remains |
| tiff `4.7.0-3+deb13u3` | 2 | FFmpeg/Tesseract dependency closures | M1, M2; one Trivy status is `will_not_fix`, the other `affected`; neither waived |
| libx11 `2:1.8.12-1` | 3 | FFmpeg/Tesseract/eSpeak NG dependency closures | M1, M2; no display socket mounted for smoke, shared libraries retained |
| libxi `2:1.8.2-1` | 2 | FFmpeg dependency closure | M1, M2; no display-server interaction exercised, no waiver |
| libxml2 `2.12.7+dfsg+really2.9.14-2.1+deb13u3` | 8 | FFmpeg/Tesseract dependency closures | M1, M3; CRITICAL C-library finding and remaining HIGH findings unresolved |
| libxrender `1:0.9.12-1` | 1 | FFmpeg/Tesseract dependency closures | M1, M2; no display-server interaction exercised, no waiver |
| perl `5.40.1-6+deb13u1` | 1 | Base maintenance dependency, not invoked by narration | M1, M4; `fix_deferred`, no force-purge or acceptance |

Removing Tesseract, FFmpeg or their loaded libraries would violate the required
runtime preservation. Merely splitting the same packages across build stages
would not patch their runtime code. A stripped/custom media build would need
separate compatibility and security review and still needs actual upstream
patches for exposed parsers; it is not substituted during this bounded fix.
Mixing unstable packages into Trixie or reverting distributions without evidence
of fixes is not a supported minimal correction.

### CVE index for every residual source package

- **util-linux:** CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410.
- **ffmpeg:** CVE-2026-58049, CVE-2026-64830, CVE-2026-64832, CVE-2026-64833,
  CVE-2026-64834, CVE-2026-64835, CVE-2026-66036, CVE-2026-66039,
  CVE-2026-66040, CVE-2026-66041, CVE-2026-70628, CVE-2026-70632,
  CVE-2026-75141, CVE-2026-75142, CVE-2026-75143, CVE-2026-75144,
  CVE-2026-75146.
- **acl:** CVE-2026-54369.
- **cjson:** CVE-2026-16554, CVE-2026-29036, CVE-2026-67215, CVE-2026-67216,
  CVE-2026-87933.
- **curl:** CVE-2026-12064, CVE-2026-8458.
- **expat:** CVE-2026-113351, CVE-2026-115506, CVE-2026-117610, CVE-2026-118370.
- **ncurses:** CVE-2025-69720.
- **libsndfile:** CVE-2026-37555.
- **sqlite3:** CVE-2026-119373, CVE-2026-119374, CVE-2026-119379.
- **systemd:** CVE-2026-16742.
- **tesseract:** CVE-2026-73066, CVE-2026-88047, CVE-2026-88048,
  CVE-2026-88051, CVE-2026-88052, CVE-2026-88053.
- **tiff:** CVE-2026-36849, CVE-2026-52490.
- **libx11:** CVE-2026-88806.
- **libxi:** CVE-2026-109089, CVE-2026-109090.
- **libxml2:** CVE-2026-6653 (CRITICAL), CVE-2026-74860, CVE-2026-86138,
  CVE-2026-86139, CVE-2026-86140, CVE-2026-86142, CVE-2026-86143,
  CVE-2026-86144.
- **libxrender:** CVE-2026-88807.
- **perl:** CVE-2026-9538.

The `really2.9.14` libxml2 version and Debian backport revisions were evaluated
using Debian's source-package status, not naive upstream version comparisons.
[CVE-2026-6653](https://security-tracker.debian.org/tracker/CVE-2026-6653) is
still open in Trixie; Debian calls it a minor issue while Trivy reports CRITICAL.
That severity disagreement is documented, not used to lower the required gate.
[CVE-2026-74860](https://security-tracker.debian.org/tracker/CVE-2026-74860)
requires Python bindings, which were absent. This is a feature-exposure
qualification, not authorization to erase a source-package scanner finding.

## One rebuild and one runtime smoke

Image configuration ID:
`sha256:dc2476c3a9f9909bcd3d035dd368f2b9af5a88fd39b899b0055587065b48346d`.
No models were downloaded. The already-existing
`oju-video-forge_narration-models` volume was mounted read-only at `/models/asr`.
Synthetic media and temporary extraction/output files existed only in container
tmpfs and were removed when the container exited.

Actual smoke results:

- faster-whisper 1.2.1 / CTranslate2 4.6.0, **real CPU int8 transcription PASS**.
- Detected English; recognized "This is a synthetic narration security test."
  at 0-2600 ms; average log probability -0.5822, no-speech probability 0.02868.
- Actual eSpeak NG speech synthesis and Tesseract title OCR passed.
- Original and derived: **6.000 seconds, 320x180, SAR 1:1, H.264, 12 fps, AAC**.
- FFprobe validation, unchanged source checksum and compressed visual-stream
  identity passed. Derived MP4: **45,788 bytes**; SHA-256
  `e4275c7309f751c17c3d62acfb78946e99361b094ea192cda3e937c48c21e9a3`.
- One ONNX Runtime telemetry-device-ID persistence warning occurred on the
  read-only filesystem. The smoke had no network; no inference data was sent
  externally. This does not claim that generic environment telemetry flags
  disable every library's telemetry in all deployments.

This smoke checks the packaged media/ASR runtime, not full API/job/browser
acceptance. Previously successful Phase 5 functionality and tests were not
reopened or rerun.

## Exact validation commands and results

Windows Docker CLI, Linux containers on ERE. No host dependency installation.
The following Docker commands use Windows PowerShell continuation syntax.

```powershell
docker build --pull --file services/narration/Dockerfile `
  --tag oju-video-forge-narration:security-dc8fa7a .
```

Exit **0**, one image build.

```powershell
docker run --rm --network none --read-only --cap-drop ALL `
  --security-opt no-new-privileges:true --cpus 2 --memory 2g --pids-limit 128 `
  --tmpfs /tmp:rw,noexec,nosuid,size=512m `
  --mount "type=volume,source=oju-video-forge_narration-models,target=/models/asr,readonly" `
  --mount "type=bind,source=${PWD}\scripts\smoke_narration_runtime.py,target=/opt/forge/smoke_narration_runtime.py,readonly" `
  -e PYTHONDONTWRITEBYTECODE=1 `
  oju-video-forge-narration:security-dc8fa7a python smoke_narration_runtime.py
```

Exit **0**, one real synthetic media/ASR smoke, all checks above passed.
`ruff check smoke_narration_runtime.py` in the existing API test image with
the new script mounted read-only also exited **0**.

```powershell
$archive = Join-Path $env:TEMP 'ovf-narration-security-dc8fa7a.tar'
docker save oju-video-forge-narration:security-dc8fa7a --output $archive
docker run --rm --cap-drop ALL --security-opt no-new-privileges:true `
  --cpus 2 --memory 2g --pids-limit 128 `
  --mount "type=bind,source=$archive,target=/scan/ovf-narration.tar,readonly" `
  --mount "type=bind,source=$evidence,target=/evidence" `
  aquasec/trivy:0.76.2 image --input /scan/ovf-narration.tar `
  --exit-code 1 --severity HIGH,CRITICAL `
  --format json --output /evidence/narration-security-after.json
```

`$evidence` was the private session evidence directory, not a public media path.
`docker save` exited **0**; Trivy exited **1** with 239 HIGH and one CRITICAL.
The image archive was removed after scanning; the JSON report remains retained.
Only JSON formatting/output flags were added to the existing repository scan
policy; vulnerability and secret scanning both stayed enabled.

No full regression, Wan inference, private-video processing, database operation,
canonical restart or scan of already-passing API/standard worker images was run.
Stop here pending supported fixes for the remaining packages. The patch can
be reviewed, but **do not merge or claim the required security job is resolved**.
