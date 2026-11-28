"""The one place a session's keywords are built, so every entry point gets the same keywords wired the same way."""
import inspect
from dataclasses import dataclass
from types import MethodType
from typing import Any, Callable, Dict, Optional

from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.app_management import AppManagement
from optics_framework.api.flow_control import FlowControl
from optics_framework.api.verifier import Verifier
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.common.session_manager import Session

KEYWORD_SOURCES = (ActionKeyword, AppManagement, FlowControl, Verifier)


def keyword_shapes() -> Dict[str, Callable[..., Any]]:
    """Every keyword with the signature a session gives it and no session behind it, for a dry run, which calls none."""
    return {
        name: MethodType(function, source)
        for source in KEYWORD_SOURCES
        for name, function in inspect.getmembers(source, inspect.isfunction)
        if not name.startswith("_")
    }


@dataclass
class Keywords:
    keyword_map: Dict[str, Callable[..., Any]]
    flow_control: FlowControl
    action_keyword: Optional[ActionKeyword] = None
    app_management: Optional[AppManagement] = None
    verifier: Optional[Verifier] = None


def build_keywords(session: Session, device: bool = True) -> Keywords:
    """Register the API classes; without ``device`` only FlowControl. A popup rule's module runs through this FlowControl."""
    registry = KeywordRegistry()
    action_keyword = app_management = verifier = None
    if device:
        action_keyword = session.optics.build(ActionKeyword)
        app_management = session.optics.build(AppManagement)
        verifier = session.optics.build(Verifier)
        verifier._popup_step = action_keyword._assert_step
        for instance in (action_keyword, app_management, verifier):
            registry.register(instance)
    flow_control = FlowControl(session=session, keyword_map=registry.keyword_map)
    registry.register(flow_control)
    if action_keyword is not None:
        action_keyword._module_runner = flow_control.execute_module
    return Keywords(registry.keyword_map, flow_control, action_keyword, app_management, verifier)
