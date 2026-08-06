#!/usr/bin/env bash
# Idempotently write Terraform's pipeline credentials into .env.
#
# Replaces the README's old `printf ... >> .env` step, which duplicated
# PIPELINE_ACCESS_KEY/PIPELINE_SECRET_KEY lines on every re-run. This script
# updates existing keys in place instead of appending, so it's safe to run
# more than once (e.g. after `terraform apply` regenerates credentials).
#
# Usage (from repo root, after `terraform apply` in infra/terraform):
#   ./scripts/configure-local-env.sh
#
# Does not print secret values. Refuses to touch .env.example. Backs up the
# existing .env to .env.bak before modifying it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
TF_DIR="$REPO_ROOT/infra/terraform"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: $ENV_FILE does not exist. Run 'cp .env.example .env' first." >&2
  exit 1
fi

if [ "$(basename "$ENV_FILE")" = ".env.example" ]; then
  echo "error: refusing to modify .env.example" >&2
  exit 1
fi

if [ ! -d "$TF_DIR" ]; then
  echo "error: $TF_DIR not found" >&2
  exit 1
fi

ACCESS_KEY="$(terraform -chdir="$TF_DIR" output -raw pipeline_access_key 2>/dev/null || true)"
SECRET_KEY="$(terraform -chdir="$TF_DIR" output -raw pipeline_secret_key 2>/dev/null || true)"

if [ -z "$ACCESS_KEY" ] || [ -z "$SECRET_KEY" ]; then
  echo "error: could not read pipeline_access_key/pipeline_secret_key from Terraform outputs." >&2
  echo "Run 'terraform apply' in $TF_DIR first." >&2
  exit 1
fi

cp "$ENV_FILE" "$ENV_FILE.bak"

set_env_var() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    # Replace the existing line in place rather than appending a duplicate.
    # Uses a temp file for portability across BSD/GNU sed (`sed -i` flag
    # syntax differs between them).
    awk -v k="$key" -v v="$value" \
      'BEGIN{FS=OFS="="} $1==k {$0=k"="v} {print}' \
      "$ENV_FILE" > "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_env_var PIPELINE_ACCESS_KEY "$ACCESS_KEY"
set_env_var PIPELINE_SECRET_KEY "$SECRET_KEY"

echo "Updated PIPELINE_ACCESS_KEY and PIPELINE_SECRET_KEY in $ENV_FILE (previous version backed up to $ENV_FILE.bak)."
echo "Secret values are not printed here — check $ENV_FILE directly if you need to inspect them."
