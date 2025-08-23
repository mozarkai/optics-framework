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
        self.MAX_REMAINING_ITEMS = 10  # Maximum items to process after stop event

        # Thread references for proper cleanup
        self.capture_thread = None
        self.dedup_thread = None

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
                execution_logger.debug(f"Captured screenshot at {timestamp} as a stream")
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

    def _process_frame_for_deduplication(self, frame, timestamp, last_processed_frame):
        """Helper method to process a single frame for deduplication."""
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if last_processed_frame is not None:
            gray_last_frame = cv2.cvtColor(last_processed_frame, cv2.COLOR_BGR2GRAY)
            similarity = ssim(gray_last_frame, gray_frame, data_range=gray_frame.max() - gray_frame.min())

            if similarity >= 0.75:
                execution_logger.debug(f"Skipping duplicate frame at {timestamp} (SSIM: {similarity:.4f})")
                return last_processed_frame

        # Frame is unique, add to filtered queue
        try:
            if self.filtered_queue.full():
                self.filtered_queue.get_nowait()
            self.filtered_queue.put_nowait((frame, timestamp))
        except queue.Full:
            internal_logger.debug("Filtered queue is full. Dropping frame.")

        return frame

    def process_screenshot_queue(self):
        """
        Continuously processes screenshots from the queue, applying SSIM-based deduplication.
        """
        last_processed_frame = None
        internal_logger.debug("Deduplication thread started.")

        # Main processing loop
        while not self.stop_event.is_set():
            try:
                frame, timestamp = self.screenshot_queue.get(timeout=0.5)
                last_processed_frame = self._process_frame_for_deduplication(frame, timestamp, last_processed_frame)
            except queue.Empty:
                continue

        # Process remaining items with limit
        remaining_items = 0
        max_remaining = self.MAX_REMAINING_ITEMS

        while remaining_items < max_remaining and not self.screenshot_queue.empty():
            try:
                frame, timestamp = self.screenshot_queue.get_nowait()
                last_processed_frame = self._process_frame_for_deduplication(frame, timestamp, last_processed_frame)
                remaining_items += 1
            except queue.Empty:
                break

        internal_logger.debug(f"Deduplication thread stopped. Processed {remaining_items} remaining items.")

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

        self.capture_thread = threading.Thread(target=self.capture_stream, args=(timeout,), daemon=True)
        self.capture_thread.start()

        if deduplication:
            self.dedup_thread = threading.Thread(target=self.process_screenshot_queue, daemon=True)
            self.dedup_thread.start()
            execution_logger.debug("Started screenshot deduplication thread.")

    def stop_capture(self, wait_for_threads=True, timeout=5):
        """
        Stops screenshot capturing and processing.

        Args:
            wait_for_threads (bool): Whether to wait for threads to finish
            timeout (int): Maximum time to wait for threads to finish
        """
        execution_logger.debug("Stopping screenshot capture...")
        self.stop_event.set()

        if wait_for_threads:
            threads_to_wait = []

            if self.capture_thread and self.capture_thread.is_alive():
                threads_to_wait.append(("Capture", self.capture_thread))

            if self.dedup_thread and self.dedup_thread.is_alive():
                threads_to_wait.append(("Deduplication", self.dedup_thread))

            for thread_name, thread in threads_to_wait:
                thread.join(timeout=timeout)
                if thread.is_alive():
                    internal_logger.warning(f"{thread_name} thread did not stop within {timeout} seconds")
                else:
                    internal_logger.debug(f"{thread_name} thread stopped successfully")

        execution_logger.debug("Screenshot capture stopped.")

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

    def get_all_available_screenshots(self, wait_time=0.1) -> list:
        """
        Retrieves all currently available screenshots from the filtered queue.

        Args:
            wait_time (float): How long to wait (non-blocking) before assuming queue is empty.

        Returns:
            List of (frame, timestamp) tuples.
        """
        frames = []
        end_time = time.time() + wait_time
        while time.time() < end_time:
            try:
                frame, timestamp = self.filtered_queue.get(timeout=0.05)
                frames.append((frame, timestamp))
            except queue.Empty:
                break
        return frames


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

    def clear_queues(self):
        """
        Clear both queues. Useful for cleanup between operations.
        """
        while not self.screenshot_queue.empty():
            try:
                self.screenshot_queue.get_nowait()
            except queue.Empty:
                break

        while not self.filtered_queue.empty():
            try:
                self.filtered_queue.get_nowait()
            except queue.Empty:
                break

        internal_logger.debug("Queues cleared.")

    def get_queue_sizes(self):
        """
        Returns the current sizes of both queues for debugging.

        Returns:
            dict: Dictionary with queue sizes
        """
        return {
            'screenshot_queue_size': self.screenshot_queue.qsize(),
            'filtered_queue_size': self.filtered_queue.qsize()
        }
