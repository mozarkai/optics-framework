import cv2
import time
import threading
import queue
from skimage.metrics import structural_similarity as ssim
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger, execution_logger

class ScreenshotStream:
    def __init__(self, capture_screenshot_callable, max_queue_size=100, debug_folder=None):
        """
        Initializes the screenshot stream helper.

        Args:
            capture_screenshot_callable (Callable): Function that captures a single screenshot.
            max_queue_size (int): Maximum size of the screenshot and filtered queues.
            debug_folder (str, optional): Folder to save debug images if needed.
        """
        self.capture_screenshot = capture_screenshot_callable
        self.screenshot_queue = queue.Queue(maxsize=max_queue_size)
        self.filtered_queue = queue.Queue(maxsize=max_queue_size)
        self.stop_event = threading.Event()
        self.debug_folder = debug_folder

    def capture_stream(self, timeout):
        """
        Continuously captures screenshots and stores them in a queue.
        Stops when `stop_event` is set or when the timeout is reached.
        """
        start_time = time.time()
        while not self.stop_event.is_set() and (time.time() - start_time) < timeout:
            timestamp = utils.get_timestamp()
            try:
                frame = self.capture_screenshot()
            except Exception as e:
                internal_logger.debug(f"ERROR: Failed to capture screenshot: {e}")
                continue

            if frame is None:
                internal_logger.debug("Screenshot capture failed. Retrying immediately.")
                continue

            try:
                if self.screenshot_queue.full():
                    self.screenshot_queue.get()
                self.screenshot_queue.put((frame, timestamp))
                internal_logger.debug(f"Screenshot added to queue at {timestamp}")
            except queue.Full:
                internal_logger.debug("Screenshot queue is full. Dropping oldest frame.")

            if self.stop_event.is_set():
                internal_logger.debug("Stop event detected. Stopping screenshot capture.")
                break

        internal_logger.debug("Screenshot capture completed after timeout or stop event.")

    def process_screenshot_queue(self):
        """
        Continuously processes screenshots from the queue, applying SSIM-based deduplication.
        """
        last_processed_frame = None

        while not self.stop_event.is_set() or not self.screenshot_queue.empty():
            try:
                frame, timestamp = self.screenshot_queue.get(timeout=1)
            except queue.Empty:
                continue

            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if last_processed_frame is not None:
                gray_last_frame = cv2.cvtColor(last_processed_frame, cv2.COLOR_BGR2GRAY)
                similarity = ssim(gray_last_frame, gray_frame, data_range=gray_frame.max() - gray_frame.min())

                if similarity >= 0.80:
                    internal_logger.debug(f"Skipping duplicate frame at {timestamp} (SSIM: {similarity:.4f})")
                    continue

            last_processed_frame = frame

            if self.filtered_queue.full():
                self.filtered_queue.get()
            self.filtered_queue.put((frame, timestamp))

    def start_capture(self, timeout, deduplication=False):
        """
        Starts screenshot capture in a separate thread.

        Args:
            timeout (int): Duration of streaming in seconds.
            deduplication (bool): Whether to run SSIM deduplication in a separate thread.
        """
        if self.stop_event.is_set():
            self.stop_event.clear()
        execution_logger.debug("Starting screenshot capture threads.")
        threading.Thread(target=self.capture_stream, args=(timeout,), daemon=True).start()
        if deduplication:
            threading.Thread(target=self.process_screenshot_queue, daemon=True).start()
            execution_logger.debug("Started screenshot deduplication thread.")

    def stop_capture(self):
        """
        Stops screenshot capturing and processing.
        """
        self.stop_event.set()

    def get_latest_screenshot(self, wait_time=1):
        """
        Fetches the latest available screenshot from the filtered queue.

        Args:
            wait_time (int): How long to wait for a screenshot (seconds).

        Returns:
            tuple: (frame, timestamp) or (None, None) if unavailable.
        """
        try:
            return self.filtered_queue.get(timeout=wait_time)
        except queue.Empty:
            return None, None

    def fetch_frames_from_queue(self, num_frames):
        """
        Fetches multiple frames from the filtered queue.

        Args:
            num_frames (int): Number of frames to fetch.

        Returns:
            list: List of (frame, timestamp) tuples.
        """
        frames_to_process = []
        while len(frames_to_process) < num_frames and not self.filtered_queue.empty():
            try:
                frame, timestamp = self.filtered_queue.get_nowait()
                frames_to_process.append((frame, timestamp))
            except queue.Empty:
                break
        return frames_to_process
