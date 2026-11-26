"""How a step's written params become a keyword's arguments: ``${name}`` values, ``key=value`` params and ``store_as``."""
import inspect
import re
from collections.abc import Iterable
from typing import Any, Callable, Dict, List, Optional, Tuple

from optics_framework.common import test_context, utils
from optics_framework.common.error import Code, OpticsError, undefined_element
from optics_framework.common.models import ElementData


_LOCATOR_PREFIXES = {"text", "css", "xpath"}


def readable_signature(method: Callable) -> Optional[inspect.Signature]:
    """The keyword's signature, or None when Python cannot read it (``str``, ``max``); such a keyword takes any params."""
    try:
        return inspect.signature(method)
    except (TypeError, ValueError):
        return None


def bind_params(name: str, method: Callable, positional: List[Any], keywords: Dict[str, Any]) -> None:
    """Refuse params the keyword cannot take, before it runs, instead of a TypeError from inside it."""
    signature = readable_signature(method)
    if signature is None:
        return
    try:
        signature.bind(*positional, **keywords)
    except TypeError as e:
        raise OpticsError(
            Code.E0403, message=f"{name} cannot take these params: {e}", hint=f"Run 'optics list {name}' to see its params."
        ) from e


def is_keyword_param(param: str) -> bool:
    """
    Checks if a parameter string is in key=value format.

    :param param: The parameter string to check.
    :type param: str
    :return: True if the parameter is in key=value format, False otherwise.
    :rtype: bool
    """
    if param.startswith(("/", "//", "(")):
        return False
    # Must have a single = that is not part of ==, !=, <=, >=
    return bool(re.search(r'(?<![=!<>])=(?!=)', param))


def split_params_by_signature(
    method: Callable, param_strings: List[str]
) -> Tuple[List[str], Dict[str, str]]:
    """Split params into (positional, keyword) using the target method's signature.

    A ``key=value`` token is treated as a keyword argument only when ``key``
    names an actual parameter of ``method``; otherwise the whole token stays
    positional. This keeps locator strategies such as ``text=...``,
    ``css=...`` or ``id=...`` as the keyword's positional ``element`` argument
    (they merely happen to contain ``=``) while still honouring genuine
    keyword arguments like ``event_name=...`` or ``timeout=...``. The first
    ``text=``/``css=``/``xpath=`` token of a keyword whose first parameter is
    ``element`` is that element unless ``element=`` is named, so
    ``Enter Text,text=Name,Ava`` locates ``text=Name`` instead of typing "Name". When the signature cannot be read the historical behaviour
    is kept (every ``key=value`` is a keyword argument).
    """
    signature = readable_signature(method)
    names = list(signature.parameters) if signature else []
    valid: Optional[set] = set(names) if signature else None
    positional: List[str] = []
    keywords: Dict[str, str] = {}
    named = {p.split("=", 1)[0].strip() for p in param_strings if is_keyword_param(p)}
    locator_first = names[:1] == ["element"] and "element" not in named
    for i, param in enumerate(param_strings):
        if is_keyword_param(param):
            key, value = param.split("=", 1)
            key = key.strip()
            element_slot = i == 0 and locator_first and key in _LOCATOR_PREFIXES
            if (valid is None or key in valid) and not element_slot:
                keywords[key] = value.strip()
                continue
        positional.append(param.strip())
    return positional, keywords


def name_locators(params: Iterable[str], locators: Callable[[str], list[str] | None]) -> None:
    """Record, for the keyword about to run, the element name each locator of its ``${name}`` params comes from; the first name wins."""
    names: dict[str, str] = {}
    for name in filter(None, map(utils.whole_var_name, params)):
        for value in locators(name) or []:
            names.setdefault(value, name)
    test_context.element_names.set(names)


def step_candidates(method: Callable, params: List[Any], elements: ElementData, undefined: Callable[[str], OpticsError] = undefined_element) -> List[List[Any]]:
    """Each param's values for the fallback ladder: every value of a whole ``${name}`` param, else the param itself."""
    raw = utils.raw_param_indices(method)
    candidates = []
    for i, param in enumerate(params):
        name = None if i in raw else utils.whole_var_name(param)
        values = [param] if name is None else elements.get_element(name)
        if not values:
            raise undefined(str(name))
        candidates.append(list(values))
    name_locators(params, elements.get_element)
    return candidates


def step_arguments(method: Callable, combo: Iterable[Any], elements: ElementData, undefined: Callable[[str], OpticsError] = undefined_element) -> Tuple[List[Any], Dict[str, Any]]:
    """One combination's positional and named args, split by the keyword's signature, with ``${name}`` resolved."""
    def resolve(param: Any) -> Any:
        name = utils.whole_var_name(param)
        if name is None:
            return param
        value = elements.get_first(name)
        if value is None:
            raise undefined(name)
        return value

    params = utils.substitute_inline(method, list(combo), elements)
    positional, named = split_params_by_signature(method, params)
    raw = utils.raw_param_indices(method)
    signature = readable_signature(method)
    raw_names = {p.name for i, p in enumerate(signature.parameters.values()) if i in raw} if signature else set()
    args = utils.unescape_vars(method, [p if i in raw else resolve(p) for i, p in enumerate(positional)])
    return args, {key: value if key in raw_names else resolve(value) for key, value in named.items()}


def resolve_scalar_param(session: Any, param: str) -> str:
    """
    Resolve a `${name}` reference from `session.elements`, returning its first value as a string.

    Only resolves when the *entire* param is exactly one `${name}` reference (anchored match) —
    a param that embeds `${var}` inside a larger expression (e.g. `${a} + ${b}`) is left
    untouched, since that's for the keyword itself to substitute (see `Evaluate`/`Condition`).
    Non-matching input is returned unchanged (stripped).
    """
    if not isinstance(param, str):
        param = str(param)
    param = param.strip()
    var_name = utils.whole_var_name(param)
    if var_name is None:
        return param
    elements = getattr(session, "elements", None)
    if not isinstance(elements, ElementData):
        raise OpticsError(Code.E0702, message=f"Cannot resolve '{param}': no element data in session.")
    value = elements.get_element(var_name)
    if value is None:
        raise undefined_element(var_name)
    if not value:
        raise OpticsError(Code.E0201, message=f"{param} has no value in the project's elements")
    return str(value[0])
