import pytest

from app.config import resolve_tls_disable_policy, runtime_env_mode, validated_gateway_token


def test_validated_gateway_token_rejects_placeholder_values():
    token, state = validated_gateway_token("replace-me-for-production")
    assert token == ""
    assert state == "placeholder-like value"


def test_validated_gateway_token_accepts_secure_value():
    token, state = validated_gateway_token("prod-token-abc-123")
    assert token == "prod-token-abc-123"
    assert state == "configured"


def test_runtime_env_mode_defaults_to_production(monkeypatch):
    monkeypatch.delenv("METROOPS_RUNTIME_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    mode, development_mode = runtime_env_mode()
    assert mode == "production"
    assert development_mode is False


def test_resolve_tls_disable_policy_rejects_production_override(monkeypatch):
    monkeypatch.setenv("METROOPS_RUNTIME_ENV", "production")
    with pytest.raises(RuntimeError, match="allowed only in development/test/local"):
        resolve_tls_disable_policy("1")


def test_resolve_tls_disable_policy_allows_development_override(monkeypatch):
    monkeypatch.setenv("METROOPS_RUNTIME_ENV", "development")
    runtime_env, tls_disabled = resolve_tls_disable_policy("1")
    assert runtime_env == "development"
    assert tls_disabled is True
