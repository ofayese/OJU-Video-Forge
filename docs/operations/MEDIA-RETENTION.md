# Private media storage and retention

Uploaded media and generated artifacts live under `ARTIFACT_ROOT`, outside the
application's public static paths. Keep this directory on private local storage or a
private Compose volume shared only with services that need it. The API uses the same
root as the worker; do not expose it through a web server or public volume.

Uploads are limited to 100 MiB, use generated storage names, and are recorded in the
`media_artifacts` manifest with the original filename, media type, byte size, and
SHA-256 digest. Retrieval checks project ownership and verifies the stored size and
digest. There is no automatic expiration or deletion endpoint. Apply an explicit,
owner-approved retention policy before removing media; never delete existing user
media as part of deployment, rollback, or routine cleanup. The `0002` migration can
be rolled back only when its project and media records are no longer needed; rollback
does not delete files from `ARTIFACT_ROOT`.
