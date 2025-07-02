import requests
import time
import subprocess
import json
from typing import Optional

BASE_URL = "http://localhost:8000"


def create_session() -> Optional[str]:
    payload = {
        "driver_sources": ["appium"],
        "elements_sources": ["appium_page_source", "appium_screenshot"],
        "text_detection": [],
        "image_detection": [],
        "project_path": None,
        "appium_url": "http://localhost:4723",
        "appium_config": {
            "appActivity": "com.google.android.apps.chrome.Main",
            "appPackage": "com.android.chrome",
            "automationName": "UiAutomator2",
            "deviceName": "emulator-5554",
            "platformName": "Android"
        }
    }
    response = requests.post(f"{BASE_URL}/v1/sessions/start", json=payload)
    print("Create Session Response:", response.status_code, response.text)
    if response.ok:
        return response.json().get("session_id")
    return None


def execute_keyword(session_id: str, keyword: str, params: Optional[list] = None):
    payload = {
        "mode": "keyword",
        "keyword": keyword,
        "params": params or []
    }
    response = requests.post(f"{BASE_URL}/v1/sessions/{session_id}/action", json=payload)
    print(f"Execute '{keyword}' Response:", response.status_code)
    if response.ok:
        print("Response JSON:", json.dumps(response.json(), indent=2))
        return response.json()
    return None


def call_named_endpoint(session_id: str, endpoint: str):
    url = f"{BASE_URL}/session/{session_id}/{endpoint}"
    response = requests.get(url)
    print(f"[GET] /{endpoint} =>", response.status_code)
    if response.ok:
        print("Response JSON:", json.dumps(response.json(), indent=2))
        return response.json()
    return None


def listen_events(session_id: str):
    try:
        print(f"Listening to events for session: {session_id}")
        curl_command = [
            "curl", "-N", f"{BASE_URL}/v1/sessions/{session_id}/events"
        ]
        process = subprocess.Popen(curl_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        for line in process.stdout:
            print("Event:", line.decode().strip())

        process.wait()
    except Exception as e:
        print("Error while running curl:", e)


def delete_session(session_id: str):
    response = requests.delete(f"{BASE_URL}/v1/sessions/{session_id}/stop")
    print("Delete Session Response:", response.status_code, response.text)


if __name__ == "__main__":
    session_id = create_session()
    if not session_id:
        print("Session creation failed. Exiting.")
        exit(1)

    # Optional: Dynamic Keyword Execution
    # execute_keyword(session_id, "press_element", ["Continue As"])
    # execute_keyword(session_id, "capture_screenshot")

    # Named endpoints (executed as pre-defined keywords)
    call_named_endpoint(session_id, "screenshot")
    # call_named_endpoint(session_id, "elements")
    # call_named_endpoint(session_id, "source")
    # call_named_endpoint(session_id, "screen_elements")

    # Event stream listener
    time.sleep(1)
    # listen_events(session_id)

    # Terminate session
    delete_session(session_id)
