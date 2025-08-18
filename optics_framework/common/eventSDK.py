import json
import os
import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common.runner.printers import TreeResultPrinter
from optics_framework.common import test_context


class EventSDK:
    def __init__(self, config_handler:ConfigHandler):
        self.config_handler = config_handler
        self.event_attributes_json_path = self.config_handler.config.event_attributes_json
        self.event_attributes_data = self._load_event_attributes_json()
        self.all_events = []
        self.real_time = False

    def _load_event_attributes_json(self):
        """Load and parse the event attributes JSON file once during initialization"""
        if not self.event_attributes_json_path:
            execution_logger.info("Event attributes JSON file path is not set")
            return {}

        try:
            with open(self.event_attributes_json_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            execution_logger.error(f"Failed to load event attributes JSON from {self.event_attributes_json_path}: {e}")
            return {}

    def get_current_time_for_events(self):
        try:
            current_utc_time = datetime.now(timezone.utc)
            desired_timezone = timezone(timedelta(hours=5, minutes=30))
            current_time_in_desired_timezone = current_utc_time.astimezone(desired_timezone)
            formatted_time = current_time_in_desired_timezone.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
            return formatted_time[:-2] + ":" + formatted_time[-2:]
        except Exception as e:
            internal_logger.error('Unable to get current time', exc_info=e)
            return None

    def check_file_availability(self, file_path):
        try:
            return os.path.exists(file_path)
        except Exception as e:
            internal_logger.error("Unable to check file availability", exc_info=e)
            return False

    def get_json_attribute(self, key):
        try:
            if not self.event_attributes_data:
                internal_logger.warning("Event attributes JSON data not loaded")
                return None

            value = self.event_attributes_data.get(key)
            if value is None:
                internal_logger.warning(f"Attribute '{key}' not found in event attributes JSON")

            return value
        except Exception as e:
            internal_logger.error(f"Unable to get {key} from JSON data", exc_info=e)
            return None

    def get_event_attributes(self, file_path):
        """Get event attributes from a separate file (used for mozark event attributes)"""
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            application_name = self.get_application_name()
            app_name = {"applicationName" : application_name}
            mozark_event_attributes = {'mozarkEventAttributes': data}
            mozark_event_attributes['mozarkEventAttributes'].update(app_name)
            return mozark_event_attributes
        except Exception as e:
            internal_logger.error("Unable to get data from Mozark event JSON", exc_info=e)
            return {}

    def set_event_name(self, event_name):
        return event_name

    def form_event_name(self, event_name):
        try:
            return {'eventName': self.set_event_name(event_name)}
        except Exception as e:
            internal_logger.error("Unable to form event name", exc_info=e)
            return {}

    def form_event_attributes(self, event_attributes):
        try:
            current_time = self.get_current_time_for_events()
            event_attributes['dateTime'] = current_time
            internal_logger.debug(f"Formed event attributes: {event_attributes}")
            return {'eventAttributes': event_attributes}
        except Exception as e:
            internal_logger.error("Unable to form event attributes", exc_info=e)
            return {}

    def submit_single_event(self, event_name, event_attributes, real_time=False, time_interval=0):
        try:
            final_event_data = self.form_event_name(event_name) | self.form_event_attributes(event_attributes)
            mozark_event_attributes = self.get_event_attributes(self.event_attributes_json_path)
            event_data = {**final_event_data, **mozark_event_attributes}
            self.all_events.append(event_data)
            self.event_sdk_initializer(real_time, time_interval)
        except Exception as e:
            internal_logger.error("Unable to form final event data", exc_info=e)

    def event_sdk_initializer(self, real_time, time_interval):
        if real_time:
            self.send_real_time_events(time_interval)
        else:
            self.send_events_after_execution()

    def send_real_time_events(self, interval):
        try:
            while self.all_events:
                threading.Timer(interval, self.send_batch_events, args=(self.all_events[:])).start()
                self.all_events.clear()
        except Exception as e:
            internal_logger.error("Unable to send real-time events", exc_info=e)

    def send_events_after_execution(self):
        try:
            while self.all_events:
                batch = self.all_events[:5]
                self.send_batch_events(batch)
                self.all_events = self.all_events[5:]
        except Exception as e:
            internal_logger.error("Unable to send events after execution", exc_info=e)

    def send_batch_events(self, event_data):
        try:
            if not event_data:
                execution_logger.info("No events to send.")
                return

            # Use the cached JSON data instead of treating the path as a dict
            event_url = self.event_attributes_data.get("eventUrl")
            bearer = self.event_attributes_data.get("testParameters_bearer")

            if not event_url:
                execution_logger.error("eventUrl not found in configuration")
                return

            if not bearer:
                execution_logger.error("testParameters_bearer not found in configuration")
                return

            url = f"{event_url}/v1/event/batchevent"
            headers = {
                'Authorization': f'Bearer {bearer}',
                'Content-Type': 'application/json'
            }
            payload = json.dumps(event_data)
            execution_logger.debug(f"Sending event to {url}: {payload}")
            response = requests.post(url, headers=headers, data=payload, timeout=10)
            response.raise_for_status()  # Raises HTTPError for bad responses
            execution_logger.info(f"Event API response: {response.text}")
            return True
        except requests.exceptions.RequestException as e:
            execution_logger.error(f"HTTP request failed: {e}")
            return False
        except Exception as e:
            execution_logger.error("Unable to send batch events", exc_info=e)
            return False

    def add_to_array(self, event_data):
        try:
            return json.dumps([event_data])
        except Exception as e:
            internal_logger.error("Unable to add events to array", exc_info=e)
            return "[]"

    def convert_to_json(self, data):
        try:
            return json.dumps(data)
        except Exception as e:
            internal_logger.error("Unable to convert to JSON", exc_info=e)
            return "{}"

    def create_events_dictionary(self, key, value):
        return {key: value}

    def nested_dictionary(self, name, key, value):
        return {name: {key: value}}

    def user_event_attributes(self, event_name, timestamp=None, **event_attributes):
        current_time = self.get_current_time_for_events() if timestamp is None else timestamp
        test_case_name = self.get_test_case_name()
        application_name = self.get_application_name()
        app_version = self.get_app_version()
        event_attributes['dateTime'] = current_time
        event_attributes['testCaseName'] = test_case_name
        event_attributes['applicationName'] = application_name
        event_attributes['applicationVersion'] = app_version
        return {'eventName': event_name, 'eventAttributes': event_attributes}

    def mozark_event_attributes(self, **event_attributes):
        return {'mozarkEventAttributes': event_attributes}

    def merge_dictionaries(self, dict1, dict2):
        return {**dict1, **dict2}

    def merge_nested_dictionaries(self, name, dict1, dict2):
        if name in dict2:
            if isinstance(dict2[name], dict):
                dict2[name].update(dict1)
            else:
                dict2[name] = {**dict1, name: dict2[name]}
        else:
            dict2[name] = dict1
        return dict2

    def print_event(self, event_data):
        try:
            printer = TreeResultPrinter.get_instance()
            printer.print_event_log(event_data)
        except Exception as e:
            internal_logger.error("Unable to print event using printer", exc_info=e)
            execution_logger.info(f"Event data: {event_data}")

    def capture_event(self, event_name, **args):
        """
        Captures events by forming the required attributes and sending them in a batch.

        Args:
            event_name (str): The name of the event.
            **args: Key-value pairs for event attributes.

        Returns:
            None
        """
        try:
            user_event_attrb = self.user_event_attributes(event_name=event_name, **args)

            # Use the file path for getting event attributes
            file_path = self.event_attributes_json_path
            if not file_path:
                raise ValueError("Event attributes JSON file path is not set.")

            event_attributes = self.get_event_attributes(file_path)
            combined_dict = self.merge_dictionaries(user_event_attrb, event_attributes)

            # Print event to console
            self.print_event(combined_dict)
            execution_logger.debug(f"Captured event: {combined_dict}")
            self.all_events.append(combined_dict)

        except Exception as e:
            execution_logger.error("Unable to capture events", exc_info=e)

    def capture_event_with_time_input(self, event_name, current_time, **event_attributes):
        """
        Captures events by forming the required attributes and sending them in a batch.

        Args:
            event_name (str): The name of the event.
            current_time (str): Timestamp for the event.
            **event_attributes: Key-value pairs for event attributes.

        Returns:
            None
        """
        try:
            user_event_attrb = self.user_event_attributes(event_name=event_name, timestamp=current_time, **event_attributes)

            # Use the file path for getting event attributes
            file_path = self.event_attributes_json_path
            if not file_path:
                raise ValueError("Event attributes JSON file path is not set.")

            mozark_event_attributes = self.get_event_attributes(file_path)
            combined_dict = self.merge_dictionaries(user_event_attrb, mozark_event_attributes)

            # Print event to console
            self.print_event(combined_dict)
            execution_logger.debug(f"Captured event: {combined_dict}")
            self.all_events.append(combined_dict)

        except Exception as e:
            execution_logger.error("Unable to capture events", exc_info=e)

    def send_all_events(self):
        if not self.all_events:
            execution_logger.info("No events to send.")
            return
        execution_logger.debug(f"Sending {len(self.all_events)} captured events...")
        # Create a copy for retry attempts
        events_to_send = self.all_events.copy()
        max_retries = 3
        retry_delay = 2  # seconds
        for attempt in range(max_retries):
            if attempt > 0:
                execution_logger.info(f"Retry attempt {attempt}/{max_retries}")
                time.sleep(retry_delay * attempt)

            success = self.send_batch_events(events_to_send)

            if success:
                # clear events after successful transmission
                self.all_events.clear()
                execution_logger.info("Events successfully sent and cleared from buffer")
                return True
            else:
                execution_logger.warning(f"Failed to send events (attempt {attempt + 1}/{max_retries})")

        # All retry attempts failed
        self.all_events.clear()
        execution_logger.info(f"Failed to send {len(events_to_send)} events after {max_retries} attempts.")
        return False

    def get_test_case_name(self):
        return test_context.current_test_case.get()

    def get_application_name(self):
        return self.get_json_attribute("applicationName")

    def get_app_version(self):
        return self.get_json_attribute("appVersion")
