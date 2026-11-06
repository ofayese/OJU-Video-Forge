# Security Gap Analysis

**State:** Initial static review; not a complete security review.

| Control | Baseline evidence | Gap / required test |
|---|---|---|
| Authentication | Shared configured bearer tokens in `app/auth.py`; no local users/sessions | Replace end-user shared tokens with persistent accounts and expiring/revocable sessions; hash credentials; test login, logout and recovery. |
| Authorization | Job status read is owner-scoped; service roles are assigned by configured token | Persist project membership/roles; check every route and artifact boundary; test horizontal and vertical privilege escalation attempts. |
| Brute-force/rate limits | Not demonstrated | Apply bounded login/API rate controls and test lockout behavior without denial-of-service side effects. |
| Input validation | Job request bounds exist; operation handling can defer unsupported values to worker | Reject unsupported job types before persistence; validate timeline/model/workflow parameter schemas. |
| File upload/path safety | Upload endpoint not found in baseline | Add size and media-type limits, content probing, randomized storage keys, path traversal protection, restrictive work directories. |
| Subprocess safety | FFmpeg test path exists; command construction not independently validated here | Use argument arrays, never interpolate user text into shell commands; enforce timeout, CPU/memory/disk limits and output validation. |
| CORS/CSRF | Not verified | Restrict CORS; use CSRF protection if cookie-based sessions are used; test browser-origin behavior. |
| Secret handling | Environment templates exist | Verify real secrets are excluded from repository/logs; rotate any accidental exposure; no credentials were inspected. |
| Container boundaries | Compose definitions exist; live app exposure not inspected | Verify least privilege, service-only networks, no unnecessary host ports, and resource limits using rendered Compose and runtime checks. |
| GPU/ComfyUI | Current local ComfyUI endpoint refused connections; workflow execution not verified | Keep ComfyUI private; expose only allowlisted generation operations; constrain workflow IDs/parameters and GPU concurrency. |
| Audit integrity | Job/audit models exist | Restrict update/delete paths and test append-only or equivalent tamper resistance; avoid secrets in audit payloads. |
| Dependencies/images | No automated scan results found | Run dependency and container vulnerability scans when tooling/registry access is available; triage results and retain reports. |

No automated vulnerability scan or penetration test was run. This document does not establish that the system is secure.
