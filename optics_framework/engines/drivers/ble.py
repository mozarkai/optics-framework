from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.logging_config import internal_logger


class BLEDriver(DriverInterface):
    """
    BLE-based implementation of the :class:`DriverInterface`.

    This driver facilitates launching applications via BLE communication.
    """

    def launch_app(self, event_name: str) -> None:
        """
        Launch an application using BLE.

        :param event_name: The event triggering the app launch.
        :type event_name: str
        """
        internal_logger.debug("Launching the BLE application.")
