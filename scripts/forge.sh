#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment="${1:-application}"
shift || true
case "$environment" in
  application) project=oju-video-forge; env_file="$root/.env"; files=(-f "$root/compose.yaml") ;;
  acceptance|integration)
    project="oju-video-forge-$environment"
    env_file="$root/.env.$environment.local"
    files=(-f "$root/compose.yaml" -f "$root/compose.$environment.yaml")
    ;;
  *) printf '%s\n' 'Unknown environment.' >&2; exit 1 ;;
esac
ollama=false
ollama_bootstrap=false
narration=false
while [[ "${1:-}" == "--narration" || "${1:-}" == "--ollama" || "${1:-}" == "--ollama-bootstrap" ]]; do
  case "$1" in
    --narration)
      if [[ "$environment" != "application" && "$environment" != "acceptance" ]]; then
        printf '%s\n' 'The narration overlay is supported only for application and acceptance.' >&2
        exit 1
      fi
      narration=true
      files+=(-f "$root/compose.narration.yaml")
      ;;
    --ollama)
      if [[ "$environment" != "application" ]]; then
        printf '%s\n' 'The Ollama overlay is supported only for the main application environment.' >&2
        exit 1
      fi
      ollama=true
      files+=(-f "$root/compose.ollama.yaml")
      ;;
    --ollama-bootstrap)
      if [[ "$environment" != "application" ]]; then
        printf '%s\n' 'Ollama bootstrap networking is supported only for the main application.' >&2
        exit 1
      fi
      ollama_bootstrap=true
      files+=(-f "$root/compose.ollama-bootstrap.yaml")
      ;;
  esac
  shift
done
if [[ "$ollama" == true && "$narration" != true ]]; then
  printf '%s\n' 'The Ollama overlay requires --narration.' >&2
  exit 1
fi
if [[ "$ollama_bootstrap" == true && "$ollama" != true ]]; then
  printf '%s\n' 'Ollama bootstrap networking requires --ollama.' >&2
  exit 1
fi
if [[ -n "${COMPOSE_PROJECT_NAME:-}${COMPOSE_FILE:-}${COMPOSE_PROFILES:-}" ]]; then
  printf '%s\n' 'Unset COMPOSE_PROJECT_NAME, COMPOSE_FILE, and COMPOSE_PROFILES first.' >&2
  exit 1
fi
for arg in "$@"; do
  case "$arg" in
    -p*|-f*|-v*|--project-name*|--project-directory*|--env-file*|--file*|--volume*|--remove-orphans*|--name*)
      printf '%s\n' 'Project, configuration, storage, and orphan overrides are not allowed.' >&2; exit 1 ;;
  esac
done
case "${1:-}" in
  build|up|start|stop|restart|ps|logs|exec|run|config|pull|images) ;;
  *) printf '%s\n' 'Unsupported operation; use stop for safe shutdown.' >&2; exit 1 ;;
esac
database_secret() {
  local line value='' count=0
  [[ -f "$1" ]] || { printf '%s\n' 'Private environment file is missing.' >&2; return 1; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    if [[ "$line" == DB_PASSWORD=* ]]; then value="${line#DB_PASSWORD=}"; count=$((count + 1)); fi
  done < "$1"
  if [[ "$count" != 1 || ! "$value" =~ ^[A-Za-z0-9_-]{32,}$ || "$value" == *REPLACE* ]]; then
    printf '%s\n' 'Use one unique URL-safe random database secret, at least 32 characters.' >&2; return 1
  fi
  printf '%s' "$value"
}
secret="$(database_secret "$env_file")"
if [[ -n "${DB_PASSWORD:-}" && "$DB_PASSWORD" != "$secret" ]]; then
  printf '%s\n' 'Shell database secret conflicts with the selected environment.' >&2; exit 1
fi
for other in "$root/.env" "$root/.env.acceptance.local" "$root/.env.integration.local"; do
  if [[ "$other" != "$env_file" && -f "$other" ]]; then
    other_secret="$(database_secret "$other")"
    if [[ "$other_secret" == "$secret" ]]; then
      printf '%s\n' 'Application and test environments must use distinct database secrets.' >&2; exit 1
    fi
  fi
done
exec docker compose --project-name "$project" --env-file "$env_file" "${files[@]}" "$@"
