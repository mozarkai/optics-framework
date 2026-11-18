import sys
from typing import Optional

from optics_framework.common.expose_api import KeywordInfo, KeywordParameter, discover_keywords
from optics_framework.common.runner.keyword_register import keyword_not_found


def _column(index: int, param: KeywordParameter) -> str:
    if param.variadic:
        return f"  param_{index}...  {param.name}..."
    default = "" if param.required else f" = {param.default}"
    return f"  param_{index}  {param.name}{default}"


def describe(info: KeywordInfo) -> str:
    """One keyword as a suite writes it: its name, its params in CSV column order, then its docstring."""
    columns = [_column(i, p) for i, p in enumerate(info.parameters, 1)]
    return "\n".join([info.keyword, *columns, "", info.description])


def main(name: Optional[str] = None) -> None:
    """Print every keyword a suite can call, or the parameters of the one named."""
    keywords = {k.keyword_slug: k for k in discover_keywords()}
    if name is None:
        print("\n".join(sorted(k.keyword for k in keywords.values() if not k.deprecated)))
        return
    info = keywords.get("_".join(name.split()).lower())
    if info is None:
        sys.exit(keyword_not_found(name, keywords).message)
    print(describe(info))
