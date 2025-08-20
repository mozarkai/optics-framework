import pytest
import tempfile
from unittest.mock import MagicMock
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.models import ElementData, ApiData, ApiCollection, ApiDefinition, RequestDefinition, ExpectedResultDefinition
from optics_framework.common.runner.test_runnner import Runner




class MockApiDefinition:
    """Shared mock API definition for testing."""

    def __init__(self, endpoint, method='GET', headers=None, body=None, extract=None, jsonpath_assertions=None):
        self.endpoint = endpoint
        self.request = type('Req', (), {'method': method, 'headers': headers or {}, 'body': body})()
        self.expected_result = type('Exp', (), {'extract': extract or {}, 'jsonpath_assertions': jsonpath_assertions or []})()


class MockApiCollection:
    """Shared mock API collection for testing."""

    def __init__(self, name, base_url, apis, global_headers=None):
        self.name = name
        self.base_url = base_url
        self.apis = apis
        self.global_headers = global_headers or {}


class MockSession:
    """Shared mock session for testing."""

    def __init__(self):
        self.elements = ElementData()
        self.modules = type('M', (), {'modules': {}, 'get_module_definition': lambda self, x: []})()
        self.apis = ApiData()
        self.apis.collections = {}
        self.config_handler = MagicMock()


@pytest.fixture
def mock_driver():
    """Fixture providing a mock driver interface."""
    # Create a MagicMock that specs to DriverInterface for call tracking
    mock = MagicMock(spec=DriverInterface)

    # Set up return values for methods that return strings
    mock.get_app_version.return_value = ""
    mock.get_text_element.return_value = ""

    # Set up validation for launch_app
    def launch_app_side_effect(app_identifier=None, app_activity=None, event_name=None):
        if event_name == "":
            raise ValueError("Event name cannot be empty.")

    mock.launch_app.side_effect = launch_app_side_effect
    return mock


@pytest.fixture
def mock_session():
    """Fixture providing a mock session."""
    return MockSession()


@pytest.fixture
def mock_runner():
    """Fixture providing a mock runner with temporary directories."""
    session = MagicMock(spec=Runner)
    session.elements = ElementData()
    session.config_handler = MagicMock()
    temp_dir = tempfile.mkdtemp()

    class Config:
        pass

    config = Config()
    config.execution_output_path = temp_dir
    config.project_path = temp_dir
    session.config_handler.config = config
    return session


@pytest.fixture
def mock_api_data():
    """Fixture providing mock API data structure."""
    return {
        "collections": {
            "authentication_apis": ApiCollection(
                name="Authentication and OTP APIs",
                base_url="http://127.0.0.1:8001",
                global_headers={},
                apis={
                    "post_token": ApiDefinition(
                        name="Token Generation",
                        description="Generate OAuth token",
                        endpoint="/token",
                        request=RequestDefinition(
                            method="POST",
                            headers={"Content-Type": "application/json"},
                            body={"username": "test", "password": "password"}
                        ),
                        expected_result=ExpectedResultDefinition(extract={"auth_token": "access_token", "user_id": "user.userId"})
                    ),
                    "send_otp": ApiDefinition(
                        name="Send OTP",
                        description="Send OTP to user",
                        endpoint="/sendotp",
                        request=RequestDefinition(
                            method="POST",
                            headers={"Authorization": "${auth_token}", "Content-Type": "application/json"},
                            body={"userId": "${user_id}", "txnType": "GEN"}
                        ),
                        expected_result={"expected_status": 200}
                    )
                }
            )
        }
    }


class MockResponse:
    """Shared mock HTTP response for testing."""

    def __init__(self, json_data=None, status_code=200, text=None):
        self.json_data = json_data or {}
        self.status_code = status_code
        self.reason = 'OK' if status_code == 200 else 'Error'
        self.headers = {'Content-Type': 'application/json'}
        self.text = text or str(json_data)
        self.content = (text or str(json_data)).encode() if text or json_data else b''

    def json(self):
        if self.json_data:
            return self.json_data
        import json
        raise json.JSONDecodeError('Expecting value', self.text, 0)

    @property
    def elapsed(self):
        class E:
            def total_seconds(self):
                return 0.01
        return E()


@pytest.fixture
def mock_response():
    """Fixture providing a mock HTTP response."""
    return MockResponse
