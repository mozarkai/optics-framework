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
    _resolve_self_heal_settings,
)


def _clear_env(monkeypatch):
    for name in (ENV_AI_SELF_HEAL, ENV_LLM_PROVIDER, ENV_LLM_MODEL):
        monkeypatch.delenv(name, raising=False)


# --- Service-level env defaults ------------------------------------------------


# --- Resolution: env default with no per-session override ----------------------


# --- Resolution: per-session overrides -----------------------------------------


def test_explicit_request_true_overrides_disabled_service_default(monkeypatch):
    _clear_env(monkeypatch)
    enabled, llm_models = _resolve_self_heal_settings(SessionConfig(ai_self_heal=True))
    assert enabled is True
    assert llm_models == [{"gemini": DependencyConfig(enabled=True, capabilities={})}]
