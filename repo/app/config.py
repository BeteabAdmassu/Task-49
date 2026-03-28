import os


def validated_gateway_token(raw_value):
    token = (raw_value or "").strip()
    if not token:
        return "", "not set"
    lowered = token.lower()
    placeholder_markers = (
        "replace-me",
        "changeme",
        "change-me",
        "placeholder",
        "example",
        "your-token",
        "your-strong-local-token",
        "set-me",
    )
    if any(marker in lowered for marker in placeholder_markers):
        return "", "placeholder-like value"
    return token, "configured"


def runtime_env_mode():
    raw_mode = (
        os.environ.get("METROOPS_RUNTIME_ENV")
        or os.environ.get("FLASK_ENV")
        or os.environ.get("APP_ENV")
        or "production"
    )
    mode = raw_mode.strip().lower()
    development_modes = {"development", "dev", "local", "test", "testing"}
    return mode, mode in development_modes


def resolve_tls_disable_policy(disable_tls_raw):
    runtime_env, development_mode = runtime_env_mode()
    tls_disable_requested = str(disable_tls_raw or "0") == "1"
    if tls_disable_requested and not development_mode:
        raise RuntimeError(
            "DISABLE_TLS_ENFORCEMENT=1 is allowed only in development/test/local runtime modes"
        )
    return runtime_env, tls_disable_requested and development_mode
