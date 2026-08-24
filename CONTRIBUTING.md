# Contributing to Optics Framework

First off, thanks for taking the time to contribute — bug reports, docs edits,
and code are all equally welcome. This guide tells you how to get productive
fast; the [Developer Guide](docs/contribution/developer_guide.md) goes deeper
on tooling, and the [architecture docs](https://mozarkai.github.io/optics-framework/architecture/)
explain how the framework is put together.

## Ways to contribute

| If you want to… | Start here |
|---|---|
| Report something broken | [Bug report form](https://github.com/mozarkai/optics-framework/issues/new?template=bug_report.yml) |
| Suggest a feature | [Feature request form](https://github.com/mozarkai/optics-framework/issues/new?template=feature_request.yml) |
| Make your first code change | [`good first issue` queue](https://github.com/mozarkai/optics-framework/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) |
| Take on something bigger | [`help wanted` queue](https://github.com/mozarkai/optics-framework/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) |
| Improve documentation | Docs live in `docs/` — preview with `poetry run mkdocs serve`. Typos/fixes need no issue; bigger rewrites, please open one first. |

Before filing, please search [existing issues](https://github.com/mozarkai/optics-framework/issues)
— if yours exists, add a 👍 and any extra detail rather than opening a duplicate.

By participating in this project you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Prerequisites: Python **3.12+**, [pipx](https://pipx.pypa.io/), git.

```bash
git clone https://github.com/mozarkai/optics-framework.git
cd optics-framework
pipx install poetry
poetry install --with dev,test,docs   # or: poetry install --with dev for code-only work
poetry run pre-commit install         # ruff, bandit, commitizen, gitleaks hooks
```

Useful commands:

```bash
poetry run pytest                     # full suite + coverage (pytest.toml)
poetry run pytest -m white_box        # unit subset only
poetry run ruff check --fix .         # lint + autofix; then ruff format .
poetry run mkdocs serve               # live-reload docs preview on :8000
poetry build                          # wheel + sdist
```

## Project layout

```
optics_framework/
├── api/            # Keyword classes: ActionKeyword, AppManagement, Verifier, FlowControl
├── common/         # Session, execution engine, self-healing strategies, errors, events
│   └── runner/     # CSV/YAML test runner, data readers, printers
├── engines/
│   ├── drivers/           # Driver backends (Appium, Selenium, Playwright, …)
│   ├── elementsources/    # Element-location sources bound to a driver
│   ├── vision_models/     # OCR (text detection) & image-detection engines
│   └── llm_models/        # LLM backends for NL mode and AI self-heal
├── helper/         # CLI commands (execute, live, serve, mcp, generate, …)
└── optics.py       # Public SDK facade / Robot Framework library
tests/
├── units/          # Unit tests (marker: white_box)
└── feature/        # Feature tests (markers: black_box, hybrid, generate)
docs/               # MkDocs site
```

## Recipes

### Adding a keyword

Adding a method to an API class is *not* enough — keywords surface through six
entry points. The complete walkthrough:

1. **Add the method** to `ActionKeyword`, `AppManagement`, `Verifier`, or
   `FlowControl` (`optics_framework/api/`). It is auto-registered for the
   CSV/YAML runner, `optics live`, `optics serve`, `optics mcp`, and
   `optics list`.
2. **If it locates an element**, decorate it with `@with_self_healing`
   (`api/action_keyword.py`) so it routes through the locator ladder
   (XPath → text → OCR → image), gets AOI/screenshot resilience and AI
   self-heal for free. The wrapped function must accept a keyword-only
   `located` parameter.
3. **Expose it on the SDK facade**: add a wrapper in `optics.py:Optics` with
   `@keyword("Pretty Name")` — without this it is invisible to Robot Framework
   and the public SDK.
4. **Teach the code generator**: add `"Pretty Name": "method_name"` to
   `TestFrameworkGenerator.keyword_registry` (`helper/generate.py`), and add
   `"Pretty Name"` to the `keyword_registry` set inside
   `YAMLDataReader.read_modules` so YAML step parsing recognises multi-word names.
5. **Test it** under `tests/units/` or `tests/feature/`, and document it.
6. **Verify**: run `optics list` and confirm reflection picks it up.

The display name (`"Pretty Name"`) is independent of the Python method name —
changing either requires touching every point above plus docs.

### Adding an engine backend

- **Driver**: subclass `DriverInterface` in
  `engines/drivers/<name>.py`; discovered by module filename matching the
  `config.yaml` key.
- **Element source**: implement `ElementSourceInterface` in
  `engines/elementsources/<name>.py`; set `REQUIRED_DRIVER_TYPE` so the factory
  injects the matching driver.
- **OCR / image detector**: implement `TextInterface` /
  `ImageInterface` under `engines/vision_models/`.
- **LLM backend**: subclass `LLMInterface` in `engines/llm_models/<name>.py`;
  selected by module filename matching the `llm_models:` config key.

See [architecture → engines](docs/architecture/engines.md) for the wiring details.

## Commit messages

Messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`, `perf:`, `style:`,
`build:`, `ci:`) — mostly because the commit-msg hook (commitizen) gently
bounces anything else. Let `poetry run cz commit` build a valid message for
you interactively, or write your own; scopes are welcome when they add
clarity: `fix(runner): advance the param-fallback ladder`.

```bash
poetry run cz commit   # interactive helper that produces a valid message
```

Signing off your commits (`git commit -s`, the
[DCO](https://developercertificate.org/)) is appreciated when convenient.
Please leave AI co-author trailers out — commits here are authored by their
human committers.

Working on something big? Splitting it into a few logical commits (say,
scaffolding → core logic → tests → docs) makes reviews nicer, but nobody is
counting.

## Pull requests

Open a pull request whenever you like — rough drafts asking for direction are
just as welcome as polished work, and early feedback beats surprises. Nothing
below is a gate:

- Reference issues with `Fixes #123` and they close automatically on merge.
- Smaller diffs tend to get reviewed faster, but ship what ships.
- The PR template sketches the description shape that reads best around here:
  **What** changed, **Why**, any **non-obvious choices** a reviewer would
  otherwise have to reconstruct, how you **validated** it, and optional
  **follow-ups**. Skip whatever doesn't apply — nobody audits headings.
- If you get a chance before pushing, `pre-commit` and `pytest` save a round
  trip:

```bash
poetry run pre-commit run --files $(git diff --name-only main)
poetry run pytest
```

CI runs the test suite, CodeQL, Scorecard, SonarQube analysis and a docs
preview on every PR anyway, so a red build is never a disaster — just fix and
push. Reviews usually land within a few days; if yours sits quietly longer, a
friendly ping on the thread is completely fine.

## Questions?

Usage questions are best asked in a [new issue](https://github.com/mozarkai/optics-framework/issues/new/choose)
with the `question` label after searching [existing ones](https://github.com/mozarkai/optics-framework/issues?q=is%3Aissue+label%3Aquestion).
You can also reach the maintainers at [lalit@mozark.ai](mailto:lalit@mozark.ai).
Security vulnerabilities follow [SECURITY.md](SECURITY.md) instead.
