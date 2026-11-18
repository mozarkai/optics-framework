from optics_framework.common.logging_config import mask_secrets, register_secret
from optics_framework.common.models import ElementData


def test_a_marked_element_stays_masked_after_the_first_stored_value():
    elements = ElementData()
    elements.add_element("pin", "@:5555")
    register_secret(elements.secrets, "title", "Backpack")

    assert mask_secrets("PIN 5555") == "PIN ****"
