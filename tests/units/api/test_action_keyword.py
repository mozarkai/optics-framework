"""ActionKeyword on a fake phone: the real strategies over a fake driver and element source."""
import pytest

from optics_framework.common.error import Code, OpticsError
from tests.units.fakes import Layer, Node, Phone, phone_keywords

COUNTRY = (50, 10)


@pytest.fixture
def keywords(tmp_path, fake_time):
    def build(*nodes):
        phone = Phone(nodes)
        return phone, phone_keywords(phone, tmp_path).action_keyword
    return build


class TestPressElement:
    def test_the_event_name_reaches_the_driver(self, keywords):
        phone, action = keywords(Node("Pay", (60, 80)))
        action.press_element("Pay", event_name="paid")
        assert (phone.taps, phone.named) == ([(60, 80)], ["paid"])


class TestEnterText:
    FIELDS = (Node("field", (5, 50)), Node("field", (50, 20)), Node("field", (50, 80)))

    @pytest.mark.parametrize(("args", "typed_at"), [
        pytest.param({}, (5, 50), id="the first match"),
        pytest.param({"index": "2"}, (50, 80), id="the match an index names"),
        pytest.param({"aoi_x": "10", "index": "1"}, (50, 20), id="a match inside the area of interest"),
    ])
    def test_the_field_it_types_into(self, keywords, args, typed_at):
        phone, action = keywords(*self.FIELDS)
        action.enter_text("field", "hello", **args)
        assert phone.typed == [(typed_at, "hello")]

    def test_a_match_outside_the_area_of_interest(self, keywords):
        phone, action = keywords(*self.FIELDS)
        with pytest.raises(OpticsError) as failure:
            action.enter_text("field", "hello", aoi_x="10")
        assert failure.value.code == Code.E0201
        assert phone.typed == []


class TestSelectDropdownOption:
    def test_an_option_the_list_does_not_have(self, keywords):
        options = Layer([Node("list", (50, 60), kind="android.widget.ListView"), Node("Only Option", (50, 40))], hides=False)
        phone, action = keywords(Node("Country", COUNTRY, opens=options))
        with pytest.raises(OpticsError) as failure:
            action.select_dropdown_option("Country", "Missing Option", timeout="30")
        assert (failure.value.code, failure.value.message) == (Code.E0201, (
            "Option 'Missing Option' not found in dropdown 'Country'. Available options: ['list', 'Only Option', 'Country']"))
        assert phone.taps == [COUNTRY]
        assert phone.swipes == [(50, 60, "up", 5)]


class TestSwipeUntilElementAppears:
    def test_an_element_already_shown(self, keywords):
        phone, action = keywords(Node("Terms", (50, 50)))
        action.swipe_until_element_appears("Terms", "up", "10")
        assert phone.swipes == []
