#!/bin/sh
set -eu

runtime_env="${METROOPS_RUNTIME_ENV:-development}"
runtime_env_lc=$(printf "%s" "$runtime_env" | tr '[:upper:]' '[:lower:]')
gateway_token="${METROOPS_GATEWAY_TOKEN:-}"
gateway_token_lc=$(printf "%s" "$gateway_token" | tr '[:upper:]' '[:lower:]')

is_placeholder_token() {
  token="$1"
  case "$token" in
    *replace-me*|*changeme*|*change-me*|*placeholder*|*example*|*your-token*|*your-strong-local-token*|*set-me*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

needs_token=0
if [ -z "$gateway_token" ]; then
  needs_token=1
elif is_placeholder_token "$gateway_token_lc"; then
  needs_token=1
fi

if [ "$needs_token" -eq 1 ]; then
  case "$runtime_env_lc" in
    development|dev|local|test|testing)
      export METROOPS_GATEWAY_TOKEN
      METROOPS_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
      printf '%s\n' "[metroops] Generated local METROOPS_GATEWAY_TOKEN for $runtime_env_lc runtime."
      ;;
    *)
      printf '%s\n' "[metroops] METROOPS_GATEWAY_TOKEN is missing or placeholder-like in $runtime_env_lc runtime." >&2
      exit 1
      ;;
  esac
fi

exec "$@"
