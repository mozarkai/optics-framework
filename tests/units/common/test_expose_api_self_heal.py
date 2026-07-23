"""Tests for AI self-heal resolution in expose_api.

Enablement and LLM provider/model are per-session settings that fall back to
service-level env-var defaults. These cover the env-only defaults and the
per-session-overrides-service-default precedence, per field.
"""
from optics_framework.common.config_handler import DependencyConfig
from optics_framework.common.expose_api import (
    ENV_AI_SELF_HEAL,
    ENV_LLM_MODEL,
    ENV_LLM_PROVIDER,
    SessionConfig,
    _env_self_heal_defaults,
    _resolve_self_heal_settings,
)


def _clear_env(monkeypatch):
    for name in (ENV_AI_SELF_HEAL, ENV_LLM_PROVIDER, ENV_LLM_MODEL):
        monkeypatch.delenv(name, raising=False)


# --- Service-level env defaults ------------------------------------------------

def test_disabled_by_default(monkeypatch):
    _clear_env(monkeypatch)
    assert _env_self_heal_defaults() == (False, None, None)


def test_env_defaults_carry_provider_and_model(monkeypatch):
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "true")
    monkeypatch.setenv(ENV_LLM_PROVIDER, "custom_llm")
    monkeypatch.setenv(ENV_LLM_MODEL, "custom-model-v1")
    assert _env_self_heal_defaults() == (True, "custom_llm", "custom-model-v1")


def test_truthy_variants_all_enable(monkeypatch):
    _clear_env(monkeypatch)
    for value in ("1", "TRUE", "Yes", "on"):
        monkeypatch.setenv(ENV_AI_SELF_HEAL, value)
        enabled, _, _ = _env_self_heal_defaults()
        assert enabled is True, f"{value!r} should enable self-heal"


def test_falsy_or_garbage_values_stay_disabled(monkeypatch):
    _clear_env(monkeypatch)
    for value in ("false", "0", "no", "", "nonsense"):
        monkeypatch.setenv(ENV_AI_SELF_HEAL, value)
        enabled, _, _ = _env_self_heal_defaults()
        assert enabled is False


# --- Resolution: env default with no per-session override ----------------------

def test_env_enabled_defaults_to_gemini(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "true")
    enabled, llm_models = _resolve_self_heal_settings(SessionConfig())
    assert enabled is True
    assert llm_models == [{"gemini": DependencyConfig(enabled=True, capabilities={})}]


def test_env_provider_and_model_flow_through(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "true")
    monkeypatch.setenv(ENV_LLM_PROVIDER, "custom_llm")
    monkeypatch.setenv(ENV_LLM_MODEL, "custom-model-v1")
    enabled, llm_models = _resolve_self_heal_settings(SessionConfig())
    assert enabled is True
    assert llm_models == [
        {"custom_llm": DependencyConfig(enabled=True, capabilities={"model": "custom-model-v1"})}
    ]


def test_disabled_returns_no_llm_models(monkeypatch):
    _clear_env(monkeypatch)
    assert _resolve_self_heal_settings(SessionConfig()) == (False, [])


# --- Resolution: per-session overrides -----------------------------------------

def test_explicit_request_false_overrides_enabled_service_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "true")
    assert _resolve_self_heal_settings(SessionConfig(ai_self_heal=False)) == (False, [])


def test_explicit_request_true_overrides_disabled_service_default(monkeypatch):
    _clear_env(monkeypatch)
    enabled, llm_models = _resolve_self_heal_settings(SessionConfig(ai_self_heal=True))
    assert enabled is True
    assert llm_models == [{"gemini": DependencyConfig(enabled=True, capabilities={})}]


def test_unset_request_field_inherits_service_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "true")
    assert _resolve_self_heal_settings(SessionConfig())[0] is True
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "false")
    assert _resolve_self_heal_settings(SessionConfig())[0] is False


def test_session_provider_and_model_override_env(monkeypatch):
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "true")
    monkeypatch.setenv(ENV_LLM_PROVIDER, "env_provider")
    monkeypatch.setenv(ENV_LLM_MODEL, "env-model")
    config = SessionConfig(llm_provider="session_provider", llm_model="session-model")
    enabled, llm_models = _resolve_self_heal_settings(config)
    assert enabled is True
    assert llm_models == [
        {"session_provider": DependencyConfig(enabled=True, capabilities={"model": "session-model"})}
    ]


def test_session_provider_falls_back_to_env_model(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_AI_SELF_HEAL, "true")
    monkeypatch.setenv(ENV_LLM_MODEL, "env-model")
    config = SessionConfig(llm_provider="session_provider")
    _, llm_models = _resolve_self_heal_settings(config)
    assert llm_models == [
        {"session_provider": DependencyConfig(enabled=True, capabilities={"model": "env-model"})}
    ]


def test_session_can_enable_and_pick_provider_with_no_env(monkeypatch):
    """A caller opts in entirely on its own — nothing set by the operator."""
    _clear_env(monkeypatch)
    config = SessionConfig(ai_self_heal=True, llm_provider="session_provider", llm_model="m1")
    enabled, llm_models = _resolve_self_heal_settings(config)
    assert enabled is True
    assert llm_models == [
        {"session_provider": DependencyConfig(enabled=True, capabilities={"model": "m1"})}
    ]
