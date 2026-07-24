"""Unit tests for KeywordExecutor surfacing AI self-heal recoveries.

KeywordExecutor.execute is the ``optics serve`` / ``optics mcp`` keyword path. When the
bound ActionKeyword method recorded a self-heal (via the ``_pop_last_heal_info`` side
channel), the executor folds ``healed`` / ``heal_summary`` / ``suggested_steps`` into the
returned dict so the synchronous HTTP/MCP payload carries them. An un-healed call returns
the bare result unchanged.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from optics_framework.common.execution import KeywordExecutor

pytestmark = pytest.mark.white_box


def _run(coro):
    return asyncio.run(coro)


class _Session:
    session_id = "sess-1"


class _Runner:
    def __init__(self, keyword_map):
        self.keyword_map = keyword_map


class _HealingKeyword:
    """Stand-in ActionKeyword: press_element passes, with a heal recorded on the side."""

    def __init__(self, heal_info):
        self._heal_info = heal_info

    def press_element(self, element):
        return None

    def _pop_last_heal_info(self):
        info, self._heal_info = self._heal_info, None
        return info


def _execute(keyword_obj, keyword="press_element", params=("Login",)):
    executor = KeywordExecutor(keyword, list(params), event_manager=AsyncMock())
    runner = _Runner({keyword: getattr(keyword_obj, keyword)})
    return _run(executor.execute(_Session(), runner))


def test_healed_keyword_returns_suggested_steps():
    heal_info = {
        "summary": "AI self-heal recovered 'press_element' after 1 step: press_element Login",
        "suggested_steps": [{"keyword": "press_element", "params": ["Login"]}],
    }
    result = _execute(_HealingKeyword(heal_info))
    assert result == {
        "result": None,
        "healed": True,
        "heal_summary": heal_info["summary"],
        "suggested_steps": [{"keyword": "press_element", "params": ["Login"]}],
    }


def test_unhealed_keyword_returns_bare_result():
    result = _execute(_HealingKeyword(None))
    assert result is None
