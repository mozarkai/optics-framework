# Global variable to store the selenium driver instance
_driver = None


def set_selenium_driver(driver):
    """Set the global selenium driver instance."""
    global _driver
    _driver = driver


def get_selenium_driver():
    """Retrieve the global selenium driver instance."""
    if _driver is None:
        raise RuntimeError(
            "selenium driver has not been initialized. Call set_driver() after starting the session.")
    return _driver


def quit_selenium_driver():
    """Quit the global selenium driver instance."""
    global _driver
    if _driver is not None:
        _driver.quit()
        _driver = None
