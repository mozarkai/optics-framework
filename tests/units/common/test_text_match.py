"""find_text: where a text locator appears among OCR detections, the same for every engine."""
import pytest

from optics_framework.common.text_match import TextMatch, find_text


def detection(text, left, top, right, bottom):
    return [(left, top), (right, top), (right, bottom), (left, bottom)], text, 0.9


def match(center, top_left, bottom_right):
    return TextMatch(center, (top_left, bottom_right))


# Google Vision on a Galaxy S22: a Play Store dialog, and app drawer labels and an icon moved below it.
GALAXY_S22 = [detection(text, *box) for text, box in [
    ("Introducing", (244, 535, 592, 594)), ("Google", (612, 538, 841, 596)),
    ("Play", (380, 626, 504, 687)), ("Points", (516, 621, 703, 683)),
    ("Earn", (194, 1159, 275, 1199)), ("points", (290, 1159, 408, 1199)), ("on", (419, 1159, 464, 1199)),
    ("purchases", (480, 1159, 675, 1199)), (",", (675, 1159, 684, 1199)), ("including", (699, 1159, 872, 1199)),
    ("in", (192, 1224, 224, 1265)), ("-", (229, 1224, 245, 1265)), ("app", (246, 1224, 318, 1265)),
    ("items", (330, 1224, 432, 1265)), (".", (433, 1224, 443, 1265)),
    ("Learn", (426, 1536, 537, 1567)), ("more", (553, 1539, 656, 1569)),
    ("Not", (457, 1691, 531, 1722)), ("now", (542, 1694, 621, 1725)),
    ("Clock", (122, 1885, 206, 1908)), ("Contacts", (350, 1884, 479, 1909)),
    ("G", (90, 2050, 235, 2190)), ("Google", (111, 2230, 215, 2258)), ("Maps", (378, 2232, 454, 2258)),
]]


@pytest.mark.parametrize(("detections", "text", "strict", "found"), [
    pytest.param([detection("Sign in", 0, 0, 140, 20)], "", True, [], id="an empty locator"),
    pytest.param([detection("Sign in", 0, 0, 140, 20)], "   ", True, [], id="a locator of spaces"),
    pytest.param([], "Sign in", True, [], id="no detections"),
    pytest.param([detection("Payment", 0, 0, 140, 20)], "Payment", True,
                 [match((70, 10), (0, 0), (140, 20))], id="one word"),
    pytest.param([detection("Payment", 0, 0, 140, 20)], "PAYMENT", True,
                 [match((70, 10), (0, 0), (140, 20))], id="a locator in other case"),
    pytest.param([detection("Sign in", 0, 0, 140, 20)], "Signin", True,
                 [match((70, 10), (0, 0), (140, 20))], id="a locator without its spaces"),
    pytest.param([detection("Sign      in", 0, 0, 120, 20)], "Sign in", True,
                 [match((60, 10), (0, 0), (120, 20))], id="extra spaces inside one detection"),
    pytest.param([detection("Sign", 0, 0, 80, 20), detection("in", 90, 0, 130, 20)], "Sign in", True,
                 [match((65, 10), (0, 0), (130, 20))], id="words from two detections on one row"),
    pytest.param([detection("Fix: plates!", 0, 0, 240, 20)], "plates", True,
                 [match((160, 10), (100, 0), (220, 20))], id="a word with punctuation around it"),
    pytest.param([detection("Ｓｉｇｎ", 0, 0, 80, 20)], "Sign", True,
                 [match((40, 10), (0, 0), (80, 20))], id="full-width letters"),
    pytest.param([detection("can't stop", 0, 0, 200, 20)], "can't", True,
                 [match((50, 10), (0, 0), (100, 20))], id="a word with an apostrophe"),
    pytest.param([detection("can't stop", 0, 0, 200, 20)], "can", True, [], id="strict, the part before an apostrophe"),
    pytest.param([detection("東京駅", 0, 0, 60, 20)], "東京", True,
                 [match((20, 10), (0, 0), (40, 20))], id="characters of a script without spaces"),
    pytest.param([detection("Payment", 0, 0, 140, 20)], "Pay", True, [], id="strict, a part of a word"),
    pytest.param([detection("Payment", 0, 0, 140, 20)], "Pay", False,
                 [match((70, 10), (0, 0), (140, 20))], id="loose, a part of a word"),
    pytest.param([detection("Payment", 10, 10, 200, 40), detection("Pay", 10, 100, 90, 130)], "Pay", False,
                 [match((50, 115), (10, 100), (90, 130)), match((105, 25), (10, 10), (200, 40))],
                 id="loose, a whole word before a part of a word above it"),
    pytest.param([detection("no no", 0, 0, 100, 20)], "no", True,
                 [match((20, 10), (0, 0), (40, 20)), match((80, 10), (60, 0), (100, 20))],
                 id="a word twice in one detection"),
    pytest.param([detection("Home", 0, 100, 80, 120), detection("Home", 200, 0, 280, 20), detection("Home", 0, 0, 80, 20)],
                 "Home", True,
                 [match((40, 10), (0, 0), (80, 20)), match((240, 10), (200, 0), (280, 20)), match((40, 110), (0, 100), (80, 120))],
                 id="matches in reading order"),
    pytest.param([detection("Continue with", 0, 0, 260, 20), detection("Google", 0, 30, 120, 50)], "Continue with Google", True,
                 [match((130, 10), (0, 0), (260, 50))], id="text wrapped onto the next line"),
    pytest.param([detection("Home", 0, 0, 80, 20), detection("Search", 300, 0, 420, 20)], "Home Search", True, [],
                 id="two far apart words on one row"),
    pytest.param([detection("מספר 12 34", 0, 0, 200, 20)], "12", True,
                 [match((80, 10), (60, 0), (100, 20))], id="the first number in right-to-left text"),
    pytest.param([detection("מספר 12 34", 0, 0, 200, 20)], "34", True,
                 [match((20, 10), (0, 0), (40, 20))], id="the last number in right-to-left text"),
    pytest.param([detection("abc שלום 12", 0, 0, 220, 20)], "12", True,
                 [match((100, 10), (80, 0), (120, 20))], id="a number after right-to-left text in a left-to-right line"),
])
def test_find_text(detections, text, strict, found):
    assert find_text(detections, text, strict) == found


@pytest.mark.parametrize("text", [
    pytest.param("Learn more Not now", id="two buttons one above the other"),
    pytest.param("more Not", id="the end of one button and the start of the next"),
    pytest.param("Clock Google", id="two app labels on rows apart"),
    pytest.param("G Google", id="an icon and the label below it"),
    pytest.param("Points Earn", id="a dialog title and its body"),
    pytest.param("Clock Contacts", id="two app labels on one row"),
])
def test_find_text_does_not_join_separate_controls(text):
    assert find_text(GALAXY_S22, text, strict=True) == []
