"""TextInterface: the match an engine's detections give a locator, and one OCR read per frame."""
import numpy as np
import pytest

from optics_framework.common.text_interface import FRAMES_REMEMBERED, TextInterface


class Replay(TextInterface):
    """An engine that detects the same lines on every frame, and counts its reads."""

    def __init__(self, *lines):
        self.lines = [([(left, top), (right, top), (right, bottom), (left, bottom)], text, 0.9)
                      for text, (left, top, right, bottom) in lines]
        self.reads = 0

    def detect_text(self, input_data):
        self.reads += 1
        return " ".join(text for _, text, _ in self.lines), self.lines


def frame(shade=0):
    return np.full((40, 60, 3), shade, dtype=np.uint8)


HOME_TWICE = (("Home", (0, 0, 80, 20)), ("Home", (0, 100, 80, 120)))


@pytest.mark.parametrize(("lines", "index", "found"), [
    pytest.param(HOME_TWICE, None, (True, (40, 10), ((0, 0), (80, 20))), id="no index"),
    pytest.param(HOME_TWICE, 1, (True, (40, 110), ((0, 100), (80, 120))), id="the second match"),
    pytest.param(HOME_TWICE, "1", (True, (40, 110), ((0, 100), (80, 120))), id="an index written as text"),
    pytest.param(HOME_TWICE, 2, None, id="an index past the last match"),
    pytest.param(HOME_TWICE, -1, None, id="a negative index"),
    pytest.param((), None, None, id="no detections"),
])
def test_find_element(lines, index, found):
    assert Replay(*lines).find_element(frame(), "Home", index=index) == found


def test_find_element_strict_refuses_a_part_of_a_word():
    assert Replay(("Payment", (0, 0, 140, 20))).find_element(frame(), "Pay", strict=True) is None


def test_find_element_on_an_engine_that_detects_nothing():
    class Blind(TextInterface):
        def detect_text(self, input_data):
            return None

    assert Blind().find_element(frame(), "Home") is None


# OCR engines are paid per read (Google Vision) or take seconds, so a frame is read once.
@pytest.mark.parametrize(("frames", "reads"), [
    pytest.param([frame(0), frame(0)], 1, id="the same frame twice"),
    pytest.param([frame(0), frame(1)], 2, id="two different frames"),
    pytest.param([frame(0), frame(0).astype(np.int16)], 2, id="the same pixels in another dtype"),
    pytest.param([frame(n) for n in range(FRAMES_REMEMBERED)] + [frame(0)], FRAMES_REMEMBERED,
                 id="a frame among the last ones read"),
    pytest.param([frame(n) for n in range(FRAMES_REMEMBERED + 1)] + [frame(0)], FRAMES_REMEMBERED + 2,
                 id="a frame read before the last ones"),
    pytest.param(["screen.png", "screen.png"], 2, id="a path, not pixels"),
])
def test_detect_text_reads_each_frame_once(frames, reads):
    engine = Replay(("Home", (0, 0, 80, 20)))
    for each in frames:
        engine.detect_text(each)
    assert engine.reads == reads
