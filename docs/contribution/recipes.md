# Recipes

Step-by-step walkthroughs for common contributions. For tooling depth (pre-commit hooks, MkDocs previews, local static analysis) see the [Developer Guide](developer_guide.md).

## Adding a keyword

Adding a method to an API class is *not* enough — keywords surface through six entry points. The complete walkthrough:

1. **Add the method** to `ActionKeyword`, `AppManagement`, `Verifier`, or `FlowControl` (`optics_framework/api/`). It is auto-registered for the CSV/YAML runner, `optics live`, `optics serve`, `optics mcp`, and `optics list`.
2. **If it locates an element**, decorate it with `@with_self_healing` (`api/action_keyword.py`) so it routes through the locator ladder (XPath → text → OCR → image), gets AOI/screenshot resilience and AI self-heal for free. The wrapped function must accept a keyword-only `located` parameter.
3. **Expose it on the SDK facade**: add a wrapper in `optics.py:Optics` with `@keyword("Pretty Name")` — without this it is invisible to Robot Framework and the public SDK.
4. **Teach the code generator**: add `"Pretty Name": "method_name"` to `TestFrameworkGenerator.keyword_registry` (`helper/generate.py`), and add `"Pretty Name"` to the `keyword_registry` set inside `YAMLDataReader.read_modules` so YAML step parsing recognises multi-word names. Making this registration simpler is tracked in [#484](https://github.com/mozarkai/optics-framework/issues/484).
5. **Write tests for it**: add new test cases under `tests/units/` or `tests/feature/` covering your keyword — running the existing suite alone is not enough, since nothing else exercises the new code path. Document the keyword too.
6. **Verify**: run `optics list` and confirm reflection picks it up.

The display name (`"Pretty Name"`) is independent of the Python method name — changing either requires touching every point above plus docs.

## Adding an engine backend

- **Driver**: subclass `DriverInterface` in `engines/drivers/<name>.py`; discovered by module filename matching the `config.yaml` key.
- **Element source**: implement `ElementSourceInterface` in `engines/elementsources/<name>.py`; set `REQUIRED_DRIVER_TYPE` so the factory injects the matching driver.
- **OCR / image detector**: implement `TextInterface` / `ImageInterface` under `engines/vision_models/`.
- **LLM backend**: subclass `LLMInterface` in `engines/llm_models/<name>.py`; selected by module filename matching the `llm_models:` config key.

See [architecture → engines](../architecture/engines.md) for the wiring details.
