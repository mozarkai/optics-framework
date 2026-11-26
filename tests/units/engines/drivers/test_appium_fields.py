from appium.webdriver.common.appiumby import AppiumBy

from tests.units.fake_appium import AppiumServer, appium_driver

ANDROID = (
    '<hierarchy rotation="0">'
    '<android.widget.LinearLayout class="android.widget.LinearLayout" bounds="[0,0][1000,400]">'
    '<android.view.View class="android.view.View" bounds="[0,0][1000,200]">'
    '<android.widget.TextView class="android.widget.TextView" text="First Name" bounds="[0,0][300,50]"/>'
    '<android.widget.EditText class="android.widget.EditText" text="" bounds="[0,60][1000,150]"/>'
    "</android.view.View>"
    '<android.view.View class="android.view.View" bounds="[0,200][1000,400]">'
    '<android.widget.TextView class="android.widget.TextView" text="Last Name" bounds="[0,200][300,250]"/>'
    '<android.widget.EditText class="android.widget.EditText" text="" bounds="[0,260][1000,350]"/>'
    "</android.view.View>"
    "</android.widget.LinearLayout>"
    '<android.view.View class="android.view.View" bounds="[0,450][1000,600]">'
    '<android.view.View class="android.view.View" bounds="[0,450][500,500]">'
    '<android.widget.TextView class="android.widget.TextView" text="Enter Mobile Number" bounds="[0,450][500,500]"/>'
    "</android.view.View>"
    '<android.widget.EditText class="android.widget.EditText" hint="Mobile Number" bounds="[0,510][1000,600]"/>'
    "</android.view.View>"
    "</hierarchy>"
)


def test_enter_text_on_a_caption_types_into_its_field(monkeypatch):
    server = AppiumServer(page=ANDROID)
    driver = appium_driver(monkeypatch, server, {"platformName": "Android", "appium:automationName": "UiAutomator2",
                                                 "appium:appPackage": "in.app"})
    driver.start_session()
    driver.enter_text_element(server.nodes(AppiumBy.XPATH, "//*[@text='Last Name']")[0], "Doe")
    assert server.commands("send_keys") == [("send_keys", "[0,260][1000,350]", "Doe")]
