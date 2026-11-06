# Evidence retention

- Keep concise, sanitized acceptance summaries and source commit references in
  version control. Preserve historical results; append corrected/current evidence
  rather than silently rewriting past outcomes.
- Do not retain raw private logs, media, prompts, backups, database exports, or
  machine inventories in the public repository. Store them only in approved private
  storage under the applicable owner retention policy.
- Test artifacts uploaded by CI should be synthetic, scoped to the run, and expire
  after 14 days unless a maintainer documents a different need.
- Do not include secrets in evidence to prove that a secret exists. Record the
  validation command and sanitized result instead.
- If private evidence is accidentally committed, stop further publication, notify
  the owner, rotate exposed credentials where authorized, and follow the approved
  incident process. Do not rewrite public history without separate approval.

Every register entry should identify the gate, requirement, status, evidence
reference, source commit, command, environment, exit code, date, and remaining gaps.
