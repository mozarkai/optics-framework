"""Fakes for what a runner reaches outside itself: the session it reads and the event manager it publishes to."""
from optics_framework.common.config_handler import Config
from optics_framework.common.models import ApiData, ElementData, ModuleData
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
