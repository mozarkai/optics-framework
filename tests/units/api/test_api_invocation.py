from unittest.mock import MagicMock
import pytest
from optics_framework.api.flow_control import FlowControl
from optics_framework.common.runner.test_runnner import Runner
from optics_framework.common.models import ApiData, ApiCollection, ApiDefinition, RequestDefinition, ElementData, ExpectedResultDefinition
from tests.mock_servers.single_server import run_single_server

# Mock ApiData structure for testing
@pytest.fixture(scope="module")
def mock_api_data():
    return ApiData(
        collections={
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
                        endpoint="/sendotp", # Now points to the single server
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
    )

@pytest.fixture(scope="module")
def live_servers():
    server_instance, thread = run_single_server()
    yield
    server_instance.should_exit = True
    thread.join()

@pytest.fixture
def mock_runner(mock_api_data):
    session = MagicMock(spec=Runner)
    session.apis = mock_api_data
    session.elements = ElementData() # Use ElementData instance for compatibility
    return session

@pytest.fixture
def flow_control(mock_runner):
    mock_runner.modules = {}
    keyword_map = {}
    flow_control = FlowControl(mock_runner, keyword_map)
    flow_control.session = mock_runner
    return flow_control

def test_invoke_api_success(flow_control, live_servers):
    # 1. Invoke the first API (post_token)
    flow_control.invoke_api("authentication_apis.post_token")

    # Assertions for the first API call
    assert flow_control.session.elements.get_element("auth_token") == "real_auth_token_123"
    assert flow_control.session.elements.get_element("user_id") == "98765"

    # 2. Invoke the second API (send_otp)
    flow_control.invoke_api("authentication_apis.send_otp")

    # No direct assertion on the second API call response here, as it's handled internally by FlowControl
    # and we're primarily testing the invocation and data flow.

def test_invoke_api_collection_not_found(flow_control):
    with pytest.raises(ValueError, match="API collection 'non_existent_apis' not found."):
        flow_control.invoke_api("non_existent_apis.some_api")

def test_invoke_api_definition_not_found(flow_control):
    with pytest.raises(ValueError, match="API 'non_existent_api' not found in collection 'Authentication and OTP APIs'."):
        flow_control.invoke_api("authentication_apis.non_existent_api")

def test_invoke_api_invalid_identifier(flow_control):
    with pytest.raises(ValueError, match="Invalid API identifier format: 'invalid_identifier'. Expected 'collection.api_name'."):
        flow_control.invoke_api("invalid_identifier")

def test_invoke_api_request_failure(flow_control, live_servers):
    # Temporarily change the base_url to a non-existent one to force a failure
    original_base_url = flow_control.session.apis.collections["authentication_apis"].base_url
    flow_control.session.apis.collections["authentication_apis"].base_url = "http://localhost:9999"

    with pytest.raises(RuntimeError, match="API request to http://localhost:9999/token failed:"):
        flow_control.invoke_api("authentication_apis.post_token")

    # Restore the original_base_url
    flow_control.session.apis.collections["authentication_apis"].base_url = original_base_url
