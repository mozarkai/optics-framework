"""Unit tests for utils.strip_page_source (condensed UI hierarchy for the LLM)."""
import pytest

from optics_framework.common import utils

pytestmark = pytest.mark.white_box

_SAMPLE = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy class="hierarchy" rotation="0">
  <android.widget.FrameLayout class="android.widget.FrameLayout" bounds="[0,0][1080,2340]">
    <android.widget.LinearLayout class="android.widget.LinearLayout" resource-id="com.sonyliv:id/action_bar_root" bounds="[0,0][1080,2340]">
      <android.widget.EditText class="android.widget.EditText" text="gullak" resource-id="com.sonyliv:id/search_edit_text" clickable="true" bounds="[26,162][1054,293]" hint="Search for movies" />
      <android.view.ViewGroup class="android.view.ViewGroup" clickable="true" bounds="[26,345][1054,1555]">
        <android.widget.TextView class="android.widget.TextView" text="Gullak - Sony LIV Originals" resource-id="com.sonyliv:id/card_show_name" bounds="[443,379][1054,517]" />
      </android.view.ViewGroup>
      <android.widget.FrameLayout class="android.widget.FrameLayout" content-desc="Home" clickable="true" bounds="[0,2067][216,2214]" />
    </android.widget.LinearLayout>
  </android.widget.FrameLayout>
</hierarchy>"""


class TestStripPageSource:
    def test_keeps_text_desc_and_interactive_nodes(self):
        out = utils.strip_page_source(_SAMPLE)
        assert 'EditText text="gullak"' in out
        assert "id=search_edit_text" in out
        assert 'hint="Search for movies"' in out
        assert 'TextView text="Gullak - Sony LIV Originals"' in out
        assert 'desc="Home"' in out
        assert "bounds=[26,162][1054,293]" in out
        assert "clickable" in out

    def test_drops_pure_layout_wrappers(self):
        out = utils.strip_page_source(_SAMPLE)
        # action_bar_root LinearLayout has a resource-id but no text/desc/interactivity.
        assert "action_bar_root" not in out
        # The outer bare FrameLayout/LinearLayout wrappers are not emitted.
        assert "LinearLayout" not in out

    def test_resource_id_package_prefix_stripped(self):
        out = utils.strip_page_source(_SAMPLE)
        assert "com.sonyliv:id/" not in out  # only the short id remains

    def test_empty_and_invalid_return_empty(self):
        assert utils.strip_page_source("") == ""
        assert utils.strip_page_source("<not valid xml") == ""

    def test_truncates_to_max_chars(self):
        out = utils.strip_page_source(_SAMPLE, max_chars=40)
        assert len(out) <= 40 + len("\n… (truncated)")
        assert out.endswith("(truncated)")
