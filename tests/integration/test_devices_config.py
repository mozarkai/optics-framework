"""config.yaml with a devices block: a wrong block stops the run and names what to fix, and other YAML keeps its meaning."""
import pytest

from optics_framework.common.config_handler import config_from

pytestmark = pytest.mark.black_box

BROWSER = """
    driver_sources:
      - playwright: {enabled: true, capabilities: {headless: true}}
"""
SHARED = """elements_sources:
  - playwright_find_element: {enabled: true}
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "test_cases.csv").write_text("test_case,test_step\nGreet,Wait\n")
    (tmp_path / "modules.csv").write_text("module_name,module_step,param_1\nWait,Sleep,0\n")
    (tmp_path / "elements.csv").write_text("Element_Name,Element_ID\nGreeting,text=Hello\n")
    return tmp_path


@pytest.fixture
def dry_run(cli):
    return lambda folder: cli("dry_run", str(folder), "--no-use-printer")


@pytest.mark.parametrize(("config", "problem"), [
    pytest.param(SHARED + "devices:\n  PH1:" + BROWSER + "    value: {pin: 1234}",
                 "devices.PH1.value", id="a key a device does not take"),
    pytest.param(SHARED + "devices:\n  PH1:" + BROWSER + "  PH2:\n    values: {pin: 1234}",
                 "devices.PH2.driver_sources", id="a device with no driver_sources"),
    pytest.param(SHARED + "devices:\n  PH1:" + BROWSER.replace("enabled: true", "enabled: false"),
                 "devices.PH1: needs an enabled driver", id="a device with no enabled driver"),
    pytest.param(SHARED + "driver_sources:\n  - playwright: {enabled: true}\ndevices:\n  PH1:" + BROWSER,
                 "playwright is enabled beside a devices block", id="a driver enabled outside the devices"),
    pytest.param(SHARED + "devices:\n  'PH 1':" + BROWSER, "'PH 1' is not a device identifier", id="a space in an identifier"),
    pytest.param(SHARED + "devices:\n  PH1:" + BROWSER + "  ph1:" + BROWSER,
                 "'PH1' and 'ph1' differ only by case", id="two identifiers that differ by case"),
    pytest.param(SHARED + "devices:\n  PH1:" + BROWSER + "    values: {pin: 0123}",
                 "devices.PH1.values.pin: put the value in quotes", id="a device value that YAML does not read as text"),
])
def test_a_wrong_devices_block_stops_the_run_and_names_the_field(project, dry_run, config, problem):
    (project / "config.yaml").write_text(config)
    result = dry_run(project)
    assert result.returncode != 0
    assert problem in " ".join(result.stderr.split())


@pytest.mark.parametrize("command", ["execute", "dry_run", "generate"])
def test_a_command_that_runs_one_device_refuses_a_devices_block(project, cli, command):
    (project / "config.yaml").write_text(SHARED + "devices:\n  PH1:" + BROWSER + "  PH2:" + BROWSER)
    result = cli(command, str(project))
    assert result.returncode != 0
    assert "has a devices block" in " ".join((result.stdout + result.stderr).replace("│", " ").split())


@pytest.mark.parametrize(("file", "devices"), [
    ("inventory.yaml", "devices:\n  pixel:\n    owner: QA\n"),
    ("config.yaml", "devices: [pixel, iphone]\n"),
], ids=["in a data file", "in the config"])
def test_a_devices_key_that_is_not_a_devices_block_keeps_its_old_meaning(project, dry_run, file, devices):
    (project / "config.yaml").write_text(SHARED + "driver_sources:\n  - playwright: {enabled: true, capabilities: {headless: true}}\n")
    with (project / file).open("a") as target:
        target.write(devices)
    assert dry_run(project).returncode == 0


def test_a_device_config_is_its_entry_over_the_shared_settings_and_shares_nothing():
    def source(name):
        return [{name: {"enabled": True}}]
    config = config_from({"elements_sources": source("playwright_find_element"), "text_detection": source("easyocr"), "devices": {
        "PH1": {"driver_sources": source("playwright"), "text_detection": source("rapidocr"), "values": {"pin": "0123"}},
        "PH2": {"driver_sources": source("selenium")}}})
    phone = config.for_device("PH1")
    phone.elements_sources[0]["playwright_find_element"].capabilities["headless"] = True
    assert [list(section[0]) for section in (phone.driver_sources, phone.elements_sources, phone.text_detection)] == [
        ["playwright"], ["playwright_find_element"], ["rapidocr"]]
    assert (phone.devices, config.for_device("PH2").elements_sources[0]["playwright_find_element"].capabilities) == ({}, {})
