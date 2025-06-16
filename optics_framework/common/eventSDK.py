from datetime import datetime, timezone, timedelta
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common.runner.printers import TreeResultPrinter
from optics_framework.common import test_context
import json
import os
import threading
import requests

class EventSDK:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = super(EventSDK, cls).__new__(cls)
            cls._instance.__init__()
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = True
            self.config_handler = ConfigHandler.get_instance()
            self.event_attributes_json = self.config_handler.get('event_attributes_json')
            self.all_events = []
            self.real_time = False

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

    def get_json_attribute(self, key):
        try:
            with open(self.event_attributes_json, 'r') as f:
                data = json.load(f)
                return data.get(key) if key in data else None
        except Exception as e:
            internal_logger.error(f"Unable to get {key} from JSON file", exc_info=e)

    def get_event_attributes(self, file_path):
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

    def set_event_name(self, event_name):
        return event_name

    def form_event_name(self, event_name):
        try:
            return {'eventName': self.set_event_name(event_name)}
        except Exception as e:
            internal_logger.error("Unable to form event name", exc_info=e)

    def form_event_attributes(self, event_attributes):
        try:
            current_time = self.get_current_time_for_events()
            event_attributes['dateTime'] = current_time
            internal_logger.debug(f"Formed event attributes: {event_attributes}")
            return {'eventAttributes': event_attributes}
        except Exception as e:
            internal_logger.error("Unable to form event attributes", exc_info=e)

    def submit_single_event(self, event_name, event_attributes, real_time=False, time_interval=0):
        try:
            final_event_data = self.form_event_name(event_name) | self.form_event_attributes(event_attributes)
            mozark_event_attributes = self.get_event_attributes(self.event_attributes_json)
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
            url = self.event_attributes_json.get("eventUrl") + "/v1/event/batchevent"
            bearer = self.event_attributes_json.get("testParameters_bearer")
            headers = {
                'Authorization': f'Bearer {bearer}',
                'Content-Type': 'application/json'
            }
            payload = json.dumps(event_data)
            execution_logger.debug(f"Sending event to {url}: {payload}")
            response = requests.post(url, headers=headers, data=payload, timeout=10)
            execution_logger.info(f"Event API response: {response.text}")
        except Exception as e:
            execution_logger.error("Unable to send batch events", exc_info=e)

    def add_to_array(self, event_data):
        try:
            return json.dumps([event_data])
        except Exception as e:
            internal_logger.error("Unable to add events to array", exc_info=e)

    def convert_to_json(self, data):
        return json.dumps(data)

    def create_events_dictionary(self, key, value):
        return {key: value}

    def nested_dictionary(self, name, key, value):
        return {name: {key: value}}

    def user_event_attributes(self, event_name,timestamp=None, **event_attributes):
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
        dict2[name].update(dict1)
        return dict2

    def print_event(self, event_data):
        printer = TreeResultPrinter.get_instance()
        printer.print_event_log(event_data)

    def capture_event(self, event_name, **args):
        """
        Captures events by forming the required attributes and sending them in a batch.

        Args:
            event_name (str): The name of the event.
            *args: Key-value pairs as strings in the format 'key=value'.

        Returns:
            None
        """
        try:
            user_event_attrb = self.user_event_attributes(event_name=event_name, **args)
            # Capture and process the events
            file_path = self.event_attributes_json
            if not file_path:
                raise ValueError("Event attributes JSON file path is not set.")

            event_attributes = self.get_event_attributes(file_path)

            combined_dict = self.merge_dictionaries(user_event_attrb, event_attributes)
            #print event to console
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
            *args: Key-value pairs as strings in the format 'key=value'.

        Returns:
            None
        """
        try:
            user_event_attrb = self.user_event_attributes(event_name=event_name,timestamp=current_time, **event_attributes)
            # Capture and process the events
            file_path = self.event_attributes_json
            if not file_path:
                raise ValueError("Environment variable 'ATTRIBUTES_JSON' is not set.")
            mozark_event_attributes = self.get_event_attributes(file_path)
            combined_dict = self.merge_dictionaries(user_event_attrb, mozark_event_attributes)
            #print event to console
            self.print_event(combined_dict)
            execution_logger.debug(f"Captured event: {combined_dict}")
            self.all_events.append(combined_dict)

        except Exception as e:
            internal_logger.error("Unable to capture events", exc_info=e)

    def send_all_events(self):
        execution_logger.info("Sending all captured events...")
        self.send_batch_events(self.all_events)


    def get_test_case_name(self):
        return test_context.current_test_case.get()

    def get_application_name(self):
        return self.get_json_attribute("applicationName")

    def get_app_version(self):
        return self.get_json_attribute("appVersion")
