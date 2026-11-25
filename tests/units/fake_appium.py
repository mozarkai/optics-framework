"""An Appium server for the Appium driver: the sessions it opens, the commands it gets and the answers it gives."""
from appium import webdriver as appium_webdriver
from appium.webdriver.common.appiumby import AppiumBy
from lxml import etree
from selenium.common.exceptions import NoSuchElementException

from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.eventSDK import EventSDK
from optics_framework.engines.drivers.appium import Appium


class Element:
    """A node of the server's page, as the client gets it back from a lookup."""

    def __init__(self, server, node):
        self.server, self.node = server, node

    @property
    def text(self):
        return self.node.get("text")

    @property
    def rect(self):
        (left, top), (right, bottom) = (map(int, corner.split(",")) for corner in
                                        self.node.get("bounds").strip("[]").split("]["))
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}

    def is_displayed(self):
        return self.node.get("displayed") != "false"

    def get_attribute(self, name):
        return self.node.get("class" if name == "className" else name)

    def send_keys(self, text):
        self.server.received("send_keys", self.node.get("bounds"), text)


class AppiumServer:
    """Each command the client sends is logged, then answered from ``answers`` by its name, or by its script's name:
    an exception is raised, a callable is called with the command's arguments, anything else is returned. A script
    that is not a ``mobile:`` command and has no answer of its own runs in the page, answered as ``"page script"``."""

    def __init__(self, answers=None, page="<hierarchy/>", device=None):
        self.answers, self.page, self.device = dict(answers or {}), page, dict(device or {})
        self.log, self.sessions = [], 0

    def received(self, command, *args):
        self.log.append((command, *args))
        answer = self.answers.get(command)
        if isinstance(answer, BaseException):
            raise answer
        return answer(*args) if callable(answer) else answer

    def commands(self, *names):
        return [entry for entry in self.log if entry[0] in names]

    def nodes(self, by, value):
        if by == AppiumBy.CLASS_NAME:
            value = f"//*[@class='{value}']"
        elif by == AppiumBy.ACCESSIBILITY_ID:
            value = f"//*[@name='{value}']"
        elif by == AppiumBy.CSS_SELECTOR:
            if not value.startswith("#"):
                raise NotImplementedError(f"the fake page finds CSS ids only, not {value!r}")
            value = f"//*[@id='{value[1:]}']"
        return [Element(self, node) for node in etree.fromstring(self.page.encode()).getroottree().xpath(value)]

    @property
    def remote(self):
        server = self

        class SwitchTo:
            @property
            def active_element(self):
                return server.received("active_element")

            def context(self, name):
                server.received("context", name)

            def window(self, handle):
                server.received("window", handle)

        class Remote:
            def __init__(self, command_executor=None, options=None, client_config=None, **_):
                reply = self.execute("newSession", {"capabilities": options.to_capabilities()})["value"]
                self.session_id, self.capabilities = reply["sessionId"], reply["capabilities"]
                self.command_executor = self
                self.switch_to = SwitchTo()

            def add_command(self, name, method, path):
                pass

            def execute(self, command, params=None):
                if command == "newSession":
                    server.sessions += 1
                    requested = params["capabilities"]
                    session_id = server.received("new_session", requested) or f"session-{server.sessions}"
                    return {"value": {"sessionId": session_id, "capabilities": {**requested, **server.device}}}
                return server.received(command, params)

            def execute_script(self, script, *args):
                if script in server.answers or script.startswith("mobile:"):
                    return server.received(script, *args)
                return server.received("page script", script, *args)

            current_package = property(lambda self: server.received("current_package"))
            current_context = property(lambda self: server.received("current_context"))
            contexts = property(lambda self: server.received("contexts"))
            window_handles = property(lambda self: server.received("window_handles"))
            current_window_handle = property(lambda self: server.received("current_window_handle"))

            @property
            def page_source(self):
                return server.page

            def find_element(self, by, value):
                found = server.nodes(by, value)
                if not found:
                    raise NoSuchElementException(value)
                return found[0]

            def find_elements(self, by, value):
                return server.nodes(by, value)

            def get_window_size(self):
                return server.received("get_window_size") or {"width": 1000, "height": 2000}

            def press_keycode(self, keycode, metastate=None):
                server.received("press_keycode", keycode)

            def activate_app(self, app_id):
                server.received("activate_app", app_id)

            def terminate_app(self, app_id):
                server.received("terminate_app", app_id)

            def quit(self):
                server.received("quit")

        return Remote


def appium_driver(monkeypatch, server, capabilities, permissions=None):
    """An Appium driver built from its config entry, whose sessions open on ``server``."""
    monkeypatch.setattr(appium_webdriver, "Remote", server.remote)
    sdk = EventSDK(ConfigHandler(Config(permissions=permissions or {})))
    return Appium({"url": "http://appium.test:4723", "capabilities": capabilities}, event_sdk=sdk)
