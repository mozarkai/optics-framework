from optics_framework.common.logging_config import internal_logger
# Global variable to store the Appium driver instance
_driver = None

def set_appium_driver(driver):
    """Set the global Appium driver instance with cleanup of old driver."""
    global _driver

    # CRITICAL: Clean up old driver if it exists
    if _driver is not None:
        old_session_id = getattr(_driver, 'session_id', 'unknown')
        internal_logger.info(f"Cleaning up old global driver with session_id: {old_session_id}")
        try:
            _driver.quit()
        except Exception as cleanup_error:
            internal_logger.warning(f"Failed to quit old global driver: {cleanup_error}")

    # Set the new driver

    _driver = driver
    new_session_id = getattr(driver, 'session_id', 'unknown') if driver else None
    internal_logger.info(f"Global driver set to new session_id: {new_session_id}")

def get_appium_driver():
    """Retrieve the global Appium driver instance with health check."""
    global _driver

    if _driver is None:
        raise RuntimeError("Appium driver has not been initialized. Call set_appium_driver() after starting the session.")

    # CRITICAL: Check if the driver is actually alive
    try:
        # Test if the session is still active
        session_id = _driver.session_id

        _driver.current_activity  # Simple test command
        internal_logger.debug(f"Global driver health check passed for session_id: {session_id}")
        return _driver
    except Exception as health_error:
        internal_logger.error(f"Global driver health check failed: {health_error}")
        # Clear the dead driver
        _driver = None
        raise RuntimeError(f"Global Appium driver is dead: {health_error}")

def quit_appium_driver():
    """Quit the global Appium driver instance with proper cleanup."""
    global _driver

    if _driver is not None:
        session_id = getattr(_driver, 'session_id', 'unknown')
        internal_logger.info(f"Quitting global driver with session_id: {session_id}")
        try:
            _driver.quit()
            internal_logger.info(f"Global driver quit successfully: {session_id}")
        except Exception as quit_error:
            internal_logger.warning(f"Failed to quit global driver {session_id}: {quit_error}")
        finally:
            _driver = None
            internal_logger.info("Global driver reference cleared")
    else:
        internal_logger.info("No global driver to quit")

def force_clear_global_driver():
    """Force clear the global driver without quitting (for emergency cleanup)."""
    global _driver
    old_session_id = getattr(_driver, 'session_id', 'unknown') if _driver else None
    _driver = None
    internal_logger.info(f"Force cleared global driver reference: {old_session_id}")
