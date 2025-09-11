import sys
import logging
import os
from typing import Optional
from mcp.server.fastmcp import FastMCP
from optics_framework.optics import Optics

log_file = os.path.expanduser("~/hello_mcp/hello_mcp.log")
logging.basicConfig(
    filename=log_file,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
print("Starting hello_mcp server...", file=sys.stderr, flush=True)

APPIUM_URL = "https://appium-hub-480385031389.asia-south1.run.app:443/wd/hub"

APPIUM_CAPABILITIES = {
  "appium:platformName": "Android",
  "appium:automationName": "UiAutomator2",
  "appium:platformVersion": "10",
  "appium:deviceName": "Redmi Note 8",
  "appium:app": "",
  "appium:udid": "a08ade64",
  "mozark:options": {
    "parentSessionId": "c5d4852c-6b3e-49f9-ba5e-71eb83a6fd90",
    "secret": "eyJraWQiOiJaNUhwM2w2dTNZMmZzWGpRR1d2aXRPMU4zR2VcL1dldm9pM09JS3BkVmx0MD0iLCJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIzMDgzODY2ZC05YTlkLTQzNmMtYWUxMS02OWNiNzQ2M2ZkNjgiLCJjb2duaXRvOmdyb3VwcyI6WyJtb3phcmsiXSwiZW1haWxfdmVyaWZpZWQiOnRydWUsImFkZHJlc3MiOnsiZm9ybWF0dGVkIjoiTkEifSwiaXNzIjoiaHR0cHM6XC9cL2NvZ25pdG8taWRwLmFwLXNvdXRoLTEuYW1hem9uYXdzLmNvbVwvYXAtc291dGgtMV9QRFlocHA1UlgiLCJjb2duaXRvOnVzZXJuYW1lIjoic2FueWFAbW96YXJrLmFpIiwicGljdHVyZSI6Imh0dHBzOlwvXC9hcHAtdGVzdC1mcm9udGVuZC5zMy5hcC1zb3V0aC0xLmFtYXpvbmF3cy5jb21cL2ltYWdlc1wvZGVmYXVsdC1waWN0dXJlLnBuZyIsIm9yaWdpbl9qdGkiOiI3ZGU0ODJkNC1iMjVmLTQ0NDAtOTkyNC03ZDJmZTQ3ZmUzMjIiLCJjdXN0b206dGVuYW50SWQiOiJtb3phcmsiLCJhdWQiOiIzcnJ2NDFuOWpoZ2NiamkxYTdtbTdhMW5kZSIsImV2ZW50X2lkIjoiODliZTZiM2EtMDAxMC00YjczLTlkN2UtOWQ3MDNjYzY4ZjRhIiwiY3VzdG9tOnVzZXJSb2xlIjoiVGVuYW50QWRtaW4iLCJ0b2tlbl91c2UiOiJpZCIsImF1dGhfdGltZSI6MTc1NzUyODYxOCwibmFtZSI6InNhbnlhIiwiZXhwIjoxNzU3NjE1MDE4LCJpYXQiOjE3NTc1Mjg2MTgsImp0aSI6Ijg4ZmMwMTk2LThmOTEtNGNjMy1hOTcxLTliZTIyMzdiZmFjYiIsImVtYWlsIjoic2FueWFAbW96YXJrLmFpIn0.CLjAiMPswAr7oRdTCvSxQTPXdl7AFdAZkBJ6IG6CXbyxsPAruMAbafkfjr4cT2-C42SL1GFcrt9QfPqJb9Ujq4z9InahytTW-CSVhvecN4P8G_xy5ojhmvH6_cCpZ2eoSXGCIYZGpRvq779FOjaISfsTbOhAhsQtIYSFU9udyKARUu2mexHakiAAdwI2J8iR-ncHwaaotRt6rzROdIAGWlHCjsUBZAfBUEKrF5jPtZ_xPhbnQ15ClhVfpaJhTDlkbxBVS02hjNiYMcf9liEKXwQGshfVrTQqGk_dYsFL9KWZqemwP-BR5Cy3h4Ou7h4z0kGfHc2DwGNM3UeC72sq3Q"
  }
}

EVENT_JSON_PATH = os.path.expanduser("~/hello_mcp/event_attributes.json")
if not os.path.exists(EVENT_JSON_PATH):
    with open(EVENT_JSON_PATH, "w") as f:
        f.write('{"event_attributes": {}}')

class HelloMCPServer:
    def __init__(self):
        self.optics: Optional[Optics] = Optics()
        self.config = {
            "driver_sources": [
                {"appium": {"enabled": True, "url": APPIUM_URL, "capabilities": APPIUM_CAPABILITIES}}
            ],
            "elements_sources": [
                {"appium_page_source": {"enabled": True, "url": None, "capabilities": {}}},
                {"appium_find_element": {"enabled": True, "url": None, "capabilities": {}}},
                {"appium_screenshot": {"enabled": True, "url": None, "capabilities": {}}},
            ],
            "text_detection": [
                {"pytesseract": {"enabled": True, "url": None, "capabilities": {}}},
            ],
            "image_detection": [
                {"templatematch": {"enabled": False, "url": None, "capabilities": {}}}
            ],
            "event_attributes_json": EVENT_JSON_PATH,
            "log_level": "DEBUG"
        }

    def setup(self) -> str:
        """Initialize Optics and launch the app (no screenshot)."""
        try:
            self.optics.setup(config=self.config)
            self.optics.launch_app()
            return {"status": "success", "message": "App launched successfully"}
        except Exception as e:
            logging.exception("Setup and launch failed")
            return {"status": "failed", "error": str(e)}

    def press_element(self, by: str):
        """Press an element on the app."""
        try:
            self.optics.press_element(by)
            return {"status": "success", "pressed": by}
        except Exception as e:
            logging.exception("Press element failed")
            return {"status": "failed", "error": str(e)}


mcp = FastMCP("hello_mcp")
server = HelloMCPServer()

@mcp.tool()
def setup():
    return server.setup()

@mcp.tool()
def press_element(by: str):
    return server.press_element(by)


if __name__ == "__main__":
    print("Running FastMCP server with stdio transport...", file=sys.stderr, flush=True)
    mcp.run(transport="stdio")
