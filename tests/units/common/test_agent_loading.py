import pytest

from optics_framework.common import agent_core
from optics_framework.common.agent_core import AgentSession, as_fail
from optics_framework.common.error import Code, OpticsError


def _entry(label: str, cls: str, y: int) -> dict:
    return {"text": label, "bounds": [0, y, 100, y + 20], "act": ["tap"], "extra": {"class": cls}}


@pytest.mark.asyncio
async def test_the_first_screen_waits_until_a_web_app_window_has_content(monkeypatch):
    monkeypatch.setattr(agent_core, "_SETTLE_PAUSE_S", 0)
    window = _entry("Spotify Premium", "window", 0)
    screens = iter([[window], [window, _entry("", "web_area", 0)], [window, _entry("Play Liked Songs", "button", 40)]])

    async def read(_png):
        return next(screens)

    session = AgentSession(read, None, {})

    snap = await session.observe(wait_loading=True)

    assert "Play Liked Songs" in {row.label for row in snap.all_rows}


def test_an_action_that_found_its_element_and_failed_says_why():
    error = OpticsError(Code.X0201, message="All strategies failed for 'Name' in 'enter_text': Spotify did not come to the front")

    fail = as_fail(error, "enter_text", ['//text_field[@name="Name"]'])

    assert "did not come to the front" in str(fail)
