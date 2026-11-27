"""A local webcam that gives fresh frames, read in this process or, where macOS refuses this process the camera, by a reader that launchd starts."""
import argparse
import ctypes
import ctypes.util
import os
import plistlib
import shutil
import socket
import struct
import subprocess  # nosec B404 - runs launchctl with fixed arguments
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger

LARGEST_SIZE = 10000
COMMON_SIZES = ((3840, 2160), (2560, 1440), (1920, 1080), (1280, 720))
SETTLE_SECONDS = 1.0
MAX_BUFFERED_FRAMES = 10
DEFAULT_FPS = 30.0
EXPOSURE_MODES = {"V4L2": (1, 3), "DSHOW": (0.25, 0.75)}
EXPOSURE_DRIFT = 0.25
# The first start waits for the person to answer the macOS camera prompt.
ANSWER_SECONDS = 75.0
OPEN_SECONDS = 15.0
POLL_SECONDS = 0.25
FRAME_HEADER = struct.Struct("<III")
SETTINGS_PATH = "System Settings > Privacy & Security > Camera"
ACCESS = {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}


class Frames(Protocol):
    def frame(self) -> np.ndarray: ...
    def release(self) -> None: ...


def camera_access() -> str | None:
    """This process's macOS camera permission ('authorized', 'denied', ...); None off macOS or when it cannot be read."""
    if sys.platform != "darwin":
        return None
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        avfoundation = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
        objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
        send = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        status = send(
            objc.objc_getClass(b"AVCaptureDevice"),
            objc.sel_registerName(b"authorizationStatusForMediaType:"),
            ctypes.c_void_p.in_dll(avfoundation, "AVMediaTypeVideo"),
        )
    except (OSError, ValueError, AttributeError) as exc:
        internal_logger.debug(f"Cannot read the camera permission: {exc}")
        return None
    return ACCESS.get(status)


class Webcam:
    """A webcam opened in this process, its focus, white balance and exposure held where the backend lets it."""

    def __init__(self, index: int, width: int | None = None, height: int | None = None) -> None:
        self.index = index
        self.cap = cv2.VideoCapture(index)
        self.period = 1 / DEFAULT_FPS
        if not self.cap.isOpened():
            return
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        _request_size(self.cap, width, height)
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.period = 1 / (fps if 1 <= fps <= 240 else DEFAULT_FPS)
        settle_until = time.monotonic() + SETTLE_SECONDS
        while time.monotonic() < settle_until:
            if not self.cap.grab():
                break
        locked = _lock_controls(self.cap)
        ok, frame = self.cap.read()
        if not ok:
            internal_logger.error(f"The camera at index {index} gives no frames")
            return
        internal_logger.info(
            f"Camera {index} ({self.cap.getBackendName()}) captures {frame.shape[1]}x{frame.shape[0]} at"
            f" {1 / self.period:.0f} fps; locked: {', '.join(locked) or 'nothing'}"
        )

    @property
    def opened(self) -> bool:
        return self.cap.isOpened()

    def frame(self) -> np.ndarray:
        """A frame exposed after this call: buffered frames come back at once, so grab until one has to wait."""
        if not self.opened:
            raise RuntimeError("No valid webcam connection.")
        for _ in range(MAX_BUFFERED_FRAMES):
            started = time.monotonic()
            if not self.cap.grab():
                break
            if time.monotonic() - started >= self.period / 3:
                break
        # The frame that waited may have started its exposure before this call.
        if self.cap.grab():
            ok, frame = self.cap.retrieve()
            if ok:
                return frame
        raise RuntimeError("Failed to capture frame from webcam.")

    def release(self) -> None:
        if self.cap.isOpened():
            self.cap.release()


def _request_size(cap: cv2.VideoCapture, width: int | None, height: int | None) -> None:
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        granted = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        if granted != (width, height):
            internal_logger.warning(f"The camera gives {granted[0]}x{granted[1]}, not {width}x{height}")
        return
    default = cap.get(cv2.CAP_PROP_FRAME_WIDTH) * cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, LARGEST_SIZE)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, LARGEST_SIZE)
    if cap.get(cv2.CAP_PROP_FRAME_WIDTH) * cap.get(cv2.CAP_PROP_FRAME_HEIGHT) > default:
        return
    for size in COMMON_SIZES:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        if (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == size:
            return


def _lock_controls(cap: cv2.VideoCapture) -> list[str]:
    """Fix focus, white balance and exposure where the backend lets it, so frames do not drift between reads."""
    locked = [name for name, prop in (("focus", cv2.CAP_PROP_AUTOFOCUS), ("white balance", cv2.CAP_PROP_AUTO_WB)) if cap.set(prop, 0)]
    modes = EXPOSURE_MODES.get(cap.getBackendName())
    ok, before = cap.read()
    if modes is None or not ok:
        return locked
    exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
    if cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, modes[0]) and cap.set(cv2.CAP_PROP_EXPOSURE, exposure):
        ok, after = cap.read()
        if ok and abs(float(after.mean()) - float(before.mean())) <= EXPOSURE_DRIFT * max(float(before.mean()), 1.0):
            return [*locked, "exposure"]
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, modes[1])
    return locked


def open_webcam(index: int, width: int | None = None, height: int | None = None) -> Frames:
    """The webcam at ``index``; on macOS, read by a launchd reader when this process may not use the camera (E0305)."""
    webcam = Webcam(index, width, height)
    if webcam.opened:
        return webcam
    access = camera_access()
    if access in (None, "authorized"):
        raise _closed(f"no camera at index {index}" if access else f"the camera at index {index} cannot be opened")
    internal_logger.info(f"This process may not use the camera ({access}); starting a camera reader through launchd")
    return WebcamReader(index, width, height)


def _closed(cause: str, hint: str | None = None) -> OpticsError:
    return OpticsError(
        Code.E0305,
        message=f"The camera does not see the device screen: {cause}",
        hint=hint or "Check camera_index; the first camera is 0.",
    )


def _python() -> str:
    """The interpreter path macOS shows in the camera prompt and in Settings: this environment's real Python."""
    return os.path.realpath(sys.executable)


class WebcamReader:
    """Frames from a reader process that launchd starts, so macOS asks the person once for this Python, whoever started Optics."""

    def __init__(self, index: int, width: int | None, height: int | None) -> None:
        self.folder = tempfile.mkdtemp(prefix="optics-cam-")
        self.label = f"org.optics-framework.camera.{uuid.uuid4().hex[:12]}"
        self.sock: socket.socket | None = None
        socket_path = os.path.join(self.folder, "s")
        command = [
            sys.executable, "-m", __name__, "--index", str(index), "--socket", socket_path,
            "--owner", str(os.getpid()), "--label", self.label,
        ]
        if width and height:
            command += ["--width", str(width), "--height", str(height)]
        try:
            self._launch(command)
            self.sock = self._connect(socket_path)
        except BaseException:
            self.release()
            raise

    def _launch(self, command: list[str]) -> None:
        package_root = str(Path(__file__).resolve().parents[3])
        job = {
            "Label": self.label,
            "ProgramArguments": command,
            "EnvironmentVariables": {"PYTHONPATH": package_root},
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardErrorPath": os.path.join(self.folder, "reader.log"),
        }
        plist = os.path.join(self.folder, "job.plist")
        with open(plist, "wb") as file:
            plistlib.dump(job, file)
        done = subprocess.run(  # nosec B603 B607 - fixed launchctl arguments
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist], capture_output=True, text=True, check=False,
        )
        if done.returncode != 0:
            raise _closed(f"launchd did not start the camera reader: {done.stderr.strip() or done.returncode}")

    def _connect(self, socket_path: str) -> socket.socket:
        error_path = socket_path + ".error"
        deadline = time.monotonic() + ANSWER_SECONDS + OPEN_SECONDS
        while time.monotonic() < deadline:
            if os.path.exists(error_path):
                cause, _, hint = Path(error_path).read_text().partition("\n")
                raise _closed(cause, hint or None)
            if os.path.exists(socket_path):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(socket_path)
                return sock
            time.sleep(POLL_SECONDS)
        raise _closed(
            f"macOS did not answer for the camera in {ANSWER_SECONDS:.0f}s",
            f"Allow the camera for {_python()} in the macOS prompt or in {SETTINGS_PATH}, then start again.",
        )

    def frame(self) -> np.ndarray:
        if self.sock is None:
            raise RuntimeError("No valid webcam connection.")
        self.sock.sendall(b"f")
        rows, columns, channels = FRAME_HEADER.unpack(_receive(self.sock, FRAME_HEADER.size))
        if not rows:
            raise RuntimeError("Failed to capture frame from webcam.")
        data = _receive(self.sock, rows * columns * channels)
        return np.frombuffer(data, np.uint8).reshape(rows, columns, channels)

    def release(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        _unload(self.label, self.folder)


def _unload(label: str, folder: str) -> None:
    """Remove the reader's files and its launchd job, which stays loaded after the reader exits."""
    shutil.rmtree(folder, ignore_errors=True)
    subprocess.run(  # nosec B603 B607 - fixed launchctl arguments
        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True, check=False,
    )


def _receive(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(min(1 << 20, size - len(data)))
        if not chunk:
            raise RuntimeError("The camera reader stopped.")
        data += chunk
    return bytes(data)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _answered(index: int, width: int | None, height: int | None) -> Webcam | str:
    """The open webcam, or why not, once the person has answered the macOS prompt that opening it raises."""
    deadline = time.monotonic() + ANSWER_SECONDS
    webcam = Webcam(index, width, height)
    while not webcam.opened and camera_access() == "not_determined" and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
    access = camera_access()
    if not webcam.opened and access == "authorized":
        webcam = Webcam(index, width, height)
    if webcam.opened:
        return webcam
    if access == "authorized":
        return f"no camera at index {index}\nCheck camera_index; the first camera is 0."
    if access == "not_determined":
        return (
            f"nobody answered the macOS camera prompt for {_python()} in {ANSWER_SECONDS:.0f}s\n"
            "Start the session again and choose Allow in the prompt."
        )
    return f"macOS does not let {_python()} use the camera ({access})\nAllow it in {SETTINGS_PATH}, then start again."


def _serve(webcam: Webcam, socket_path: str, owner: int) -> None:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path + ".tmp")
    os.chmod(socket_path + ".tmp", 0o600)
    os.rename(socket_path + ".tmp", socket_path)
    server.listen(1)
    server.settimeout(1.0)
    while _alive(owner):
        try:
            client, _ = server.accept()
        except TimeoutError:
            continue
        with client:
            client.settimeout(1.0)
            while _alive(owner):
                try:
                    request = client.recv(1)
                except TimeoutError:
                    continue
                if not request:
                    return
                try:
                    frame = webcam.frame()
                except RuntimeError:
                    client.sendall(FRAME_HEADER.pack(0, 0, 0))
                    continue
                client.sendall(FRAME_HEADER.pack(*frame.shape) + np.ascontiguousarray(frame).tobytes())
        return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve a webcam's frames to one Optics session over a Unix socket.")
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--owner", type=int, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    args = parser.parse_args(argv)
    opened = _answered(args.index, args.width, args.height)
    if isinstance(opened, str):
        Path(args.socket + ".error").write_text(opened)
        return
    try:
        _serve(opened, args.socket, args.owner)
    finally:
        opened.release()
        # Last: unloading the job ends this process.
        _unload(args.label, os.path.dirname(args.socket))


if __name__ == "__main__":
    main()
