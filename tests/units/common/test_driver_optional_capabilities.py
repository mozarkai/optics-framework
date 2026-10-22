import pytest

from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.error import Code, OpticsError

pytestmark = pytest.mark.white_box


class _BareDriver(DriverInterface):
    pass


_BareDriver.__abstractmethods__ = frozenset()


def test_an_optional_capability_a_driver_lacks_raises_e0105():
    driver = _BareDriver()

    with pytest.raises(OpticsError) as excinfo:
        driver.go_back()

    assert excinfo.value.code == Code.E0105
    assert "'go_back' is not supported by the _BareDriver driver." in str(excinfo.value)
