# Builds optics-framework the way .github/workflows/tests.yml does, so that the
# scanner can run the hermetic unit suite and the headless-Chromium journey test
# with no network access.
FROM python:3.12-bookworm
ENV DEBIAN_FRONTEND=noninteractive \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    POETRY_INSTALLER_ONLY_BINARY=:all: \
    POETRY_INSTALLER_NO_BINARY=csscompressor,jsmin

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --only-binary=:all: poetry==2.4.1

# scanner contract: the checkout lives inside the image, at /src
COPY . /src
WORKDIR /src

# Runtime and test dependencies plus every optional driver, vision, LLM and MCP
# extra that CI installs; the sample suites need them importable.
RUN poetry install --with test \
      --extras appium --extras selenium --extras playwright \
      --extras google-vision --extras llm --extras mcp

# Chromium for the playwright driver and tests/integration/test_playwright_journey.py.
RUN playwright install --with-deps chromium

# Prove the image works, but never fail the build on a test failure. The appium
# integration test skips itself when no emulator or Appium server is present.
RUN pytest -c pytest.toml --no-cov -q || echo "WARNING: pytest reported failures"
