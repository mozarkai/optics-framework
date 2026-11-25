"""Fakes for what the runner and the keywords reach outside the process: a session, an event manager, a phone and a model."""
import json
import os
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from optics_framework.api.keywords import build_keywords
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.elementsource_interface import ElementSourceInterface, ScreenPoint
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.models import ApiData, ElementData, ModuleData
from optics_framework.common.strategies import StrategyManager
from optics_framework.common.text_interface import TextInterface
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


def given(value):
    """``value``, or raised when it is an exception."""
    if isinstance(value, Exception):
        raise value
    return value


def elements(**values):
    data = ElementData()
    for name, locators in values.items():
        for locator in locators:
            data.add_element(name, locator)
    return data


@dataclass(eq=False)
class Node:
    """One element on the phone. A tap on it ``closes`` its layer or ``opens`` another. Only OCR reads an ``ocr`` node,
    template matching finds an ``image`` node by that template's name, and a ``web`` node is found as a web page's
    point, which carries its text and state, not as an element handle."""

    text: str
    at: tuple[int, int]
    resource_id: str = ""
    kind: str = "android.widget.TextView"
    closes: bool = False
    opens: "Layer | None" = None
    ocr: bool = False
    image: str = ""
    web: bool = False
    value: str = ""
    enabled: bool = True
    readable: bool = True


@dataclass
class Layer:
    """A popup over the app. A tap on one of its closing nodes removes it and brings up ``then``."""

    nodes: list[Node]
    hides: bool = True
    then: "Layer | None" = None


class Phone:
    """An app's nodes under popup layers. A lookup sees past no layer that ``hides``; any layer takes a tap on the app.
    A scroll down brings the ``below`` nodes into view."""

    def __init__(self, app, *layers, below=()):
        self.app, self.layers, self.below = list(app), list(layers), list(below)
        self.lookups, self.taps, self.swipes, self.typed, self.named, self.source_reads = [], [], [], [], [], 0

    def scroll(self, direction):
        self.swipes.append(direction)
        if direction == "down":
            self.app, self.below = self.app + self.below, []

    def shown(self):
        nodes = [node for layer in self.layers for node in layer.nodes]
        return nodes if any(layer.hides for layer in self.layers) else nodes + self.app

    def find(self, text, index=0):
        self.lookups.append(text)
        matches = [node for node in self.shown() if node.text == text and not node.ocr]
        return matches[index] if index < len(matches) else None

    def tap(self, x, y):
        self.taps.append((x, y))
        for layer in self.layers:
            if any(node.at == (x, y) and node.closes for node in layer.nodes):
                self.layers.remove(layer)
                self.layers.extend([layer.then] if layer.then else [])
                return
        if self.layers:
            raise RuntimeError("the popup took the tap")
        opened = next((node.opens for node in self.app if node.at == (x, y) and node.opens), None)
        self.layers.extend([opened] if opened else [])

    def page_source(self):
        self.source_reads += 1
        nodes = [node for node in [*self.app, *(n for layer in self.layers for n in layer.nodes)] if not node.ocr]
        return "<hierarchy>" + "".join(
            f'<n text="{n.text}" resource-id="{n.resource_id}" package="in.app" '
            f'bounds="[{n.at[0] - 5},{n.at[1] - 5}][{n.at[0] + 5},{n.at[1] + 5}]"/>' for n in nodes
        ) + "</hierarchy>"


def _box(node):
    return (node.at[0] - 5, node.at[1] - 5), (node.at[0] + 5, node.at[1] + 5)


class PhoneDriver(DriverInterface):
    """An Android driver of ``phone`` on a 100 x 100 window: taps, swipes and typing reach it."""

    def __init__(self, phone):
        self.phone = phone

    def _name(self, event_name):
        self.phone.named.extend([event_name] if event_name else [])

    def press_coordinates(self, coor_x, coor_y, event_name=None):
        self._name(event_name)
        self.phone.tap(coor_x, coor_y)

    def press_element(self, element, repeat, event_name=None):
        self._name(event_name)
        self.phone.tap(*element.at)

    def enter_text_element(self, element, text, event_name=None):
        self._name(event_name)
        self.phone.typed.append((element.at, text))

    def scroll(self, direction, duration=1000, event_name=None):
        self.phone.scroll(direction)

    def swipe(self, x_coor, y_coor, direction, swipe_length, event_name=None):
        self.phone.swipes.append((x_coor, y_coor, direction, swipe_length))

    def swipe_percentage(self, x_percentage, y_percentage, direction, swipe_percentage, event_name=None):
        self.phone.swipes.append((x_percentage, y_percentage, direction, swipe_percentage))

    def get_text_element(self, element):
        if not element.readable:
            raise RuntimeError("stale element")
        return element.value or element.text

    def is_element_enabled(self, element):
        return element.enabled

    def get_window_size(self):
        return {"width": 100, "height": 100}

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
        node = self.phone.find(element, int(index or 0))
        if node is not None and node.web:
            return ScreenPoint(*node.at, text=node.value or node.text, enabled=node.enabled)
        return node

    def get_bbox_for_element(self, element):
        return _box(element)

    def check_elements(self, elements, visible=False):
        return [self.phone.find(element) is not None for element in elements]

    def assert_elements(self, elements, timeout=30, rule="any"):
        if not any(self.check_elements(elements)):
            raise TimeoutError(elements)

    assert_elements_visible = assert_elements

    def get_page_source(self):
        return self.phone.page_source(), "ts"

    def get_interactive_elements(self, filter_config=None, compact=False):
        return [{"text": node.text, "xpath": f"//*[@text='{node.text}']", "extra": {"class": node.kind},
                 "bounds": dict(zip(("x1", "y1", "x2", "y2"), (*_box(node)[0], *_box(node)[1])))}
                for node in self.phone.shown() if not node.ocr]


class PhoneOCR(TextInterface):
    """An OCR engine that reads the text of every node shown on ``phone``."""

    def __init__(self, phone):
        self.phone = phone

    def detect_text(self, input_data):
        lines = [([(left, top), (right, top), (right, bottom), (left, bottom)], node.text, 0.9)
                 for node in self.phone.shown() for (left, top), (right, bottom) in [_box(node)]]
        return " ".join(text for _, text, _ in lines), lines


class PhoneImages(ImageInterface):
    """Template matching that finds the nodes shown on ``phone`` whose ``image`` is the template's name."""

    def __init__(self, phone):
        self.phone = phone

    def find_element(self, input_data, image, index=None):
        matches = [node for node in self.phone.shown() if node.image and node.image == os.path.basename(str(image))]
        position = int(index or 0)
        return (True, matches[position].at, _box(matches[position])) if position < len(matches) else None

    def element_exist(self, input_data, reference_data):
        found = self.find_element(input_data, reference_data)
        return (True, found[1], list(found[2])) if found else (False, (None, None), None)

    def assert_elements(self, input_data, elements, rule="any"):
        hits = [self.find_element(input_data, element) is not None for element in elements]
        return (any(hits) if rule == "any" else all(hits)), input_data


class ScriptedModel(LLMInterface):
    """A self-heal model that sends ``replies`` in turn (raising one that is an exception), then gives up, and keeps
    each prompt it was sent."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt, images=None, system=None, response_schema=None, *, temperature=None):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else {"reason": "Nothing on the screen fits.", "action": "give_up"}
        return json.dumps(given(reply))


class PhoneBuilder:
    """The builder a session gives the API classes, over ``phone``'s driver and element source. An ``llm`` that is an
    exception is what building the LLM raises."""

    def __init__(self, phone, config, llm=None, ocr=False):
        driver = PhoneDriver(phone)
        self.session_config, self.event_sdk, self.session = config, None, None
        self._driver, self._source = InstanceFallback([driver]), InstanceFallback([PhoneSource(phone, driver)])
        self._llm = InstanceFallback([llm]) if isinstance(llm, LLMInterface) else llm
        self._ocr = InstanceFallback([PhoneOCR(phone)]) if ocr else None

    def get_driver(self):
        return self._driver

    def get_element_source(self):
        return self._source

    def get_text_detection(self):
        return self._ocr

    def get_image_detection(self):
        return None

    def get_llm(self):
        return given(self._llm)

    def build(self, cls):
        return cls(self)


def phone_screen(phone, ocr=False, images=False, sources=None):
    """A StrategyManager over ``phone``'s element source, or ``sources``, with OCR and template matching when asked."""
    return StrategyManager(
        InstanceFallback(sources or [PhoneSource(phone, PhoneDriver(phone))]),
        InstanceFallback([PhoneOCR(phone)]) if ocr else None,
        InstanceFallback([PhoneImages(phone)]) if images else None,
    )


def phone_keywords(phone, output, rules=(), modules=None, llm=None, ocr=False):
    """The keywords of a session on ``phone``, built by build_keywords as every entry point builds them."""
    config = Config(popup_rules=list(rules), execution_output_path=str(output), ai_self_heal=llm is not None)
    session = SimpleNamespace(optics=PhoneBuilder(phone, config, llm, ocr), modules=ModuleData(modules=modules or {}),
                              elements=ElementData(), config_handler=ConfigHandler(config))
    return build_keywords(session)


class FakeTime:
    """A clock for the keywords' waits: a sleep moves it on at once, and each read moves it on a millisecond."""

    def __init__(self):
        self.now, self.slept = 0.0, []

    def monotonic(self):
        self.now += 0.001
        return self.now

    time = monotonic

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    async def async_sleep(self, seconds):
        self.sleep(seconds)
