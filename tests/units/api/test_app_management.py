"""Tests for AppManagement — a thin delegation layer over the driver."""
from types import SimpleNamespace

import pytest

from optics_framework.api.app_management import AppManagement


@pytest.fixture
def app_management(mock_driver):
    builder = SimpleNamespace(get_driver=lambda: mock_driver)
    return AppManagement(builder)


class TestAppManagement:
    def test_init_tolerates_missing_driver(self):
        # A None driver is logged, not raised, at construction time.
        AppManagement(SimpleNamespace(get_driver=lambda: None))

    def test_initialise_setup_is_noop(self, app_management):
        assert app_management.initialise_setup() is None

    @pytest.mark.parametrize(
        "method, args, driver_attr, expected_args, expected_kwargs",
        [
            ("launch_app", ("com.x",), "launch_app", (),
             {"app_identifier": "com.x", "app_activity": None, "event_name": None}),
            ("launch_other_app", ("com.y", "ev"), "launch_other_app", ("com.y", "ev"), {}),
            ("close_and_terminate_app", (), "terminate", (), {}),
            ("force_terminate_app", ("com.z", "ev"), "force_terminate_app", ("com.z", "ev"), {}),
            ("start_appium_session", ("ev",), "launch_app", ("ev",), {}),
            ("get_driver_session_id", (), "get_driver_session_id", (), {}),
        ],
    )
    def test_delegates_to_driver(self, app_management, mock_driver, method, args,
                                 driver_attr, expected_args, expected_kwargs):
        getattr(app_management, method)(*args)
        getattr(mock_driver, driver_attr).assert_called_once_with(*expected_args, **expected_kwargs)

    def test_launch_app_returns_driver_result(self, app_management, mock_driver):
        mock_driver.launch_app.return_value = "session-123"
        assert app_management.launch_app("com.x") == "session-123"

    def test_get_app_version_without_package(self, app_management, mock_driver):
        mock_driver.get_app_version.return_value = "1.2.3"
        assert app_management.get_app_version() == "1.2.3"
        mock_driver.get_app_version.assert_called_once_with()

    def test_get_app_version_with_package(self, app_management, mock_driver):
        mock_driver.get_app_version.return_value = "4.5.6"
        assert app_management.get_app_version("com.x") == "4.5.6"
        mock_driver.get_app_version.assert_called_once_with(app_package="com.x")
