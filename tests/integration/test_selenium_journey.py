"""Web journeys through a chromedriver WebDriver server against the local fixture site."""

import pathlib
import socket
import subprocess
import time

import pytest

pytest.importorskip("selenium")

from selenium.webdriver.common.selenium_manager import SeleniumManager  # noqa: E402

from optics_framework.helper.execute import run_project  # noqa: E402

pytestmark = pytest.mark.black_box

FIXTURE = pathlib.Path(__file__).parent / "web_fixture"

CONFIG = """\
driver_sources:
  - selenium:
      enabled: true
      url: {url}
      capabilities:
        browserName: chrome
        goog:chromeOptions:
          args: ["--headless=new"]
elements_sources:
  - selenium_find_element:
      enabled: true
  - selenium_page_source:
      enabled: true
  - selenium_screenshot:
      enabled: true
"""

TEST_CASES = """\
test_case,test_step
Suite Setup,Open Store
Shop,Journey
Suite Teardown,Close Store
"""

ELEMENTS = """\
Element_Name,Element_ID
password_field,css=#password
orders_link,css=#welcome a
"""


@pytest.fixture(scope="module")
def server_url():
    try:
        chromedriver = SeleniumManager().binary_paths(["--browser", "chrome", "--offline"])["driver_path"]
    except Exception as e:
        pytest.skip(f"chromedriver is not installed: {e}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen(
        [chromedriver, f"--port={port}", "--url-base=/wd/hub"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            with socket.socket() as ready:
                if ready.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/wd/hub"
    finally:
        server.kill()
        server.wait()


def _project(tmp_path, server_url, steps):
    modules = [
        "module_name,module_step,param_1,param_2",
        f"Open Store,Launch App,{(FIXTURE / 'index.html').as_uri()}",
        *(f"Journey,{step}" for step in steps),
        "Close Store,Close And Terminate App",
    ]
    files = {
        "config.yaml": CONFIG.format(url=server_url),
        "test_cases/test_cases.csv": TEST_CASES,
        "modules/modules.csv": "\n".join(modules) + "\n",
        "test_data/elements.csv": ELEMENTS,
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_suite_replays_a_sign_in_journey(tmp_path, server_url):
    project = _project(tmp_path, server_url, [
        "Enter Text,${password_field},secret",
        "Press Element,Sign in",
        "Assert Presence,Welcome",
        "Press Element,${orders_link}",
        "Assert Presence,Your orders",
        "Go Back",
        "Assert Presence,Optics Store",
    ])
    assert run_project(project, use_printer=False) is True


def test_a_missing_element_fails_the_run(tmp_path, server_url):
    project = _project(tmp_path, server_url, ["Assert Presence,css=#not-on-the-page,2"])
    assert run_project(project, use_printer=False) is False
