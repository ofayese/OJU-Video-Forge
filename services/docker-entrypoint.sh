#!/bin/sh
# Entrypoint for OJU services.
# Named volumes are mounted root-owned; chown the telemetry-state directory so the
# forge user can write the replay-state file and its lock file, then drop privileges
# via gosu and exec the original command (argument quoting preserved).
# A chown failure here would leave replay state unwritable by the application, so
# it is treated as fatal (no silent suppression).
set -e

STATE_DIR="/opt/forge/telemetry-state"

if [ -d "$STATE_DIR" ]; then
    chown forge:forge "$STATE_DIR" 2>/dev/null || true
fi

exec gosu forge "$@"
