# Contributing to Optics Framework

First off, thanks for taking the time to contribute — bug reports and code are equally welcome. This guide tells you how to get productive fast; [CLAUDE.md](CLAUDE.md) maps how the framework is put together.

## Development setup

Prerequisites: Python **3.12+**, [pipx](https://pipx.pypa.io/), git.

```bash
git clone git@github.com:mozarkai/optics-framework.git
cd optics-framework
pipx install poetry
poetry install --with dev,test
poetry run pre-commit install         # ruff, bandit, commitizen, gitleaks hooks
```

Useful commands:

```bash
poetry run pytest                     # full suite + coverage (pytest.toml)
poetry run pytest -m white_box        # unit subset only
poetry run ruff check --fix .         # lint + autofix; then ruff format .
poetry build                          # wheel + sdist
```

## Ways to contribute

| If you want to… | Start here |
|---|---|
| Report something broken | [Bug report form](https://github.com/mozarkai/optics-framework/issues/new?template=bug_report.yml) |
| Suggest a feature | [Feature request form](https://github.com/mozarkai/optics-framework/issues/new?template=feature_request.yml) |
| Take on something bigger | [`help wanted` queue](https://github.com/mozarkai/optics-framework/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) |

Before filing, please search [existing issues](https://github.com/mozarkai/optics-framework/issues) — if yours exists, add a 👍 and any extra detail rather than opening a duplicate.

By participating in this project you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Recipes

Adding a keyword end to end and adding an engine backend are walked through in [CLAUDE.md](CLAUDE.md): see "Checklist when adding a keyword" and "Where else to put things" before your first code change.

## Commit messages

Messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`, `perf:`, `style:`, `build:`, `ci:`) — mostly because the commit-msg hook (commitizen) gently bounces anything else. Let `poetry run cz commit` build a valid message for you interactively, or write your own; scopes are welcome when they add clarity: `fix(runner): advance the param-fallback ladder`.

```bash
poetry run cz commit   # interactive helper that produces a valid message
```

Merge commits are not allowed. We use rebase here: rebase your branch onto `main` before opening a PR and keep it rebased until merge (`git fetch origin && git rebase origin/main`).

Signing off your commits (`git commit -s`, the [DCO](https://developercertificate.org/)) is appreciated when convenient. Please leave AI co-author trailers out — commits here are authored by their human committers.

Working on something big? Splitting it into a few logical commits (say, scaffolding → core logic → tests) makes reviews nicer, but nobody is counting.

## Pull requests

Open a pull request whenever you like — rough drafts asking for direction are just as welcome as polished work, and early feedback beats surprises. Nothing below is a gate:

- Reference issues with `Fixes #123` and they close automatically on merge.
- Smaller diffs tend to get reviewed faster, but ship what ships.
- The PR template sketches the description shape that reads best around here: **What** changed, **Why**, any **non-obvious choices** a reviewer would otherwise have to reconstruct, how you **validated** it beyond CI (tests added, manual steps tried), and optional **follow-ups**. Skip whatever doesn't apply — nobody audits headings.
- If you get a chance before pushing, `pre-commit` and `pytest` save a round trip:

```bash
poetry run pre-commit run --files $(git diff --name-only main)
poetry run pytest
```

CI runs the test suite, pre-commit, CodeQL and SonarCloud on every PR anyway, so a red build is never a disaster — just fix and push. Reviews usually land within a few days; if yours sits quietly longer, a friendly ping on the thread is completely fine.

## Questions?

Usage questions are best asked in a [new issue](https://github.com/mozarkai/optics-framework/issues/new/choose) with the `question` label after searching [existing ones](https://github.com/mozarkai/optics-framework/issues?q=is%3Aissue+label%3Aquestion). You can also reach the maintainers at [lalit@mozark.ai](mailto:lalit@mozark.ai). Security vulnerabilities follow [SECURITY.md](SECURITY.md) instead.
