
from optics_framework.common import utils


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
    def test_empty_and_invalid_return_empty(self):
        assert utils.strip_page_source("") == ""
        assert utils.strip_page_source("<not valid xml") == ""

    def test_truncates_to_max_chars(self):
        out = utils.strip_page_source(_SAMPLE, max_chars=40)
        assert len(out) <= 40 + len("\n… (truncated)")
        assert out.endswith("(truncated)")

    def test_secure_and_password_fields_are_interactive(self):
        # A SecureTextField (by class suffix) and a password="true" node are kept
        # even without any clickable flag.
        xml = (
            "<hierarchy>"
            "<XCUIElementTypeSecureTextField class='XCUIElementTypeSecureTextField' "
            "name='pin' x='0' y='0' width='10' height='10'/>"
            "<android.widget.EditText class='android.widget.EditText' "
            "resource-id='pkg:id/pwd' password='true' bounds='[0,0][5,5]'/>"
            "</hierarchy>"
        )
        out = utils.strip_page_source(xml)
        assert "XCUIElementTypeSecureTextField" in out
        assert 'resource-id="pkg:id/pwd"' in out
