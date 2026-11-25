"""Fakes for what the runner and the keywords reach outside the process: a session, an event manager, a phone and a model."""
import json
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from optics_framework.api.keywords import build_keywords
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.elementsource_interface import ElementSourceInterface, ScreenPoint
from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.models import ApiData, ElementData, ModuleData
from optics_framework.engines.drivers.appium_popup import AppiumScreen
from optics_framework.helper.execute import build_linked_list

UNTIMED = ("entity_id", "parent_id", "timestamp", "start_time", "end_time", "elapsed", "logs")


class FakeSession:
    """A session with no driver: the test cases, modules and elements a runner reads, and captures off."""

    def __init__(self, test_cases, modules, elements=None, session_id="s1"):
        self.session_id = session_id
        self.modules = ModuleData(modules=modules)
        self.test_cases = build_linked_list(test_cases, self.modules)
        self.elements = elements if elements is not None else ElementData()
        self.apis = ApiData()
        self.config = Config(save_captures=False, halt_duration=0)
        self.driver = None


class RecordingEvents:
    """An event manager that keeps what is published and never sends a command."""

    def __init__(self):
        self.events = []

    async def publish_event(self, event):
        self.events.append(event)

    async def get_command(self):
        return None

    def records(self):
        """Each event without its ids and times, which a test cannot know."""
        return [{k: v for k, v in event.model_dump().items() if k not in UNTIMED} for event in self.events]


def elements(**values):
    data = ElementData()
    for name, locators in values.items():
        for locator in locators:
            data.add_element(name, locator)
    return data


@dataclass
class Node:
    text: str
    at: tuple[int, int]
    resource_id: str = ""
    closes: bool = False


@dataclass
class Layer:
    """A popup over the app. A tap on one of its closing nodes removes it and brings up ``then``."""

    nodes: list[Node]
    hides: bool = True
    then: "Layer | None" = None


class Phone:
    """An app's nodes under popup layers. A lookup sees past no layer that ``hides``; any layer takes a tap on the app."""

    def __init__(self, app, *layers):
        self.app, self.layers = list(app), list(layers)
        self.lookups, self.taps, self.source_reads = [], [], 0

    def find(self, text):
        self.lookups.append(text)
        shown = [node for layer in self.layers for node in layer.nodes]
        if not any(layer.hides for layer in self.layers):
            shown += self.app
        return next((node for node in shown if node.text == text), None)

    def tap(self, x, y):
        self.taps.append((x, y))
        for layer in self.layers:
            if any(node.at == (x, y) and node.closes for node in layer.nodes):
                self.layers.remove(layer)
                self.layers.extend([layer.then] if layer.then else [])
                return
        if self.layers:
            raise RuntimeError("the popup took the tap")

    def page_source(self):
        self.source_reads += 1
        nodes = [*self.app, *(node for layer in self.layers for node in layer.nodes)]
        return "<hierarchy>" + "".join(
            f'<n text="{n.text}" resource-id="{n.resource_id}" package="in.app" '
            f'bounds="[{n.at[0] - 5},{n.at[1] - 5}][{n.at[0] + 5},{n.at[1] + 5}]"/>' for n in nodes
        ) + "</hierarchy>"


class PhoneDriver(DriverInterface):
    """An Android driver of ``phone``: taps reach it, and its page source is read as Appium's."""

    def __init__(self, phone):
        self.phone = phone

    def press_coordinates(self, coor_x, coor_y, event_name=None):
        self.phone.tap(coor_x, coor_y)

    def popup_screen(self, source):
        return AppiumScreen(source)

    def read_alert(self):
        return None


PhoneDriver.__abstractmethods__ = frozenset()


class PhoneSource(ElementSourceInterface):
    """What ``phone`` shows: its nodes by text, a white screenshot and its page source."""

    def __init__(self, phone, driver):
        self.phone, self.driver = phone, driver

    def capture(self):
        return np.full((100, 100, 3), 255, dtype=np.uint8)

    def locate(self, element, index=None):
        node = self.phone.find(element)
        return None if node is None else ScreenPoint(*node.at, text=node.text, enabled=True)

    def check_elements(self, elements, visible=False):
        return [self.phone.find(element) is not None for element in elements]

    def assert_elements(self, elements, timeout=30, rule="any"):
        if not any(self.check_elements(elements)):
            raise TimeoutError(elements)

    assert_elements_visible = assert_elements

    def get_page_source(self):
        return self.phone.page_source(), "ts"

    def get_interactive_elements(self, filter_config=None, compact=False):
        return []


class GivingUpModel(LLMInterface):
    """A self-heal model that gives up at once and keeps each prompt it was sent."""

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, images=None, system=None, response_schema=None, *, temperature=None):
        self.prompts.append(prompt)
        return json.dumps({"reason": "Nothing on the screen fits.", "action": "give_up"})


class PhoneBuilder:
    """The builder a session gives the API classes, over ``phone``'s driver and element source."""

    def __init__(self, phone, config, llm=None):
        driver = PhoneDriver(phone)
        self.session_config, self.event_sdk, self.session = config, None, None
        self._driver, self._source = InstanceFallback([driver]), InstanceFallback([PhoneSource(phone, driver)])
        self._llm = InstanceFallback([llm]) if llm else None

    def get_driver(self):
        return self._driver

    def get_element_source(self):
        return self._source

    def get_text_detection(self):
        return None

    def get_image_detection(self):
        return None

    def get_llm(self):
        return self._llm

    def build(self, cls):
        return cls(self)


def phone_keywords(phone, output, rules=(), modules=None, llm=None):
    """The keywords of a session on ``phone``, built by build_keywords as every entry point builds them."""
    config = Config(popup_rules=list(rules), execution_output_path=str(output), ai_self_heal=llm is not None)
    session = SimpleNamespace(optics=PhoneBuilder(phone, config, llm), modules=ModuleData(modules=modules or {}),
                              elements=ElementData(), config_handler=ConfigHandler(config))
    return build_keywords(session)
