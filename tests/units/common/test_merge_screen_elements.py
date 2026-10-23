from optics_framework.common.strategies import merge_screen_elements


def _entry(text, x1, y1, x2, y2, **extra):
    return {"text": text, "bounds": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}, **extra}


def test_web_entries_replace_the_native_nodes_that_mirror_them():
    heading = _entry("Optics Shop", 24, 86, 378, 124)
    native = [
        _entry("Sign in", 24, 700, 378, 752),  # same box as the web button
        _entry("Optics Shop", 24, 86, 213, 124),  # XCUITest sizes the text to its words
        _entry("Optics", 24, 86, 120, 124),  # different text inside the heading stays
        _entry("", 0, 0, 402, 874, act=["scroll"]),  # the native container to scroll stays
    ]
    web = [heading, _entry("Sign in", 24, 700, 378, 752)]

    merged = merge_screen_elements(native, web)

    assert [entry["text"] for entry in merged] == ["Optics", "", "Optics Shop", "Sign in"]
