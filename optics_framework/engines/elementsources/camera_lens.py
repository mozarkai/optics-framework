"""Manual focus for a webcam, so its lens holds on the device screen instead of hunting."""
import ctypes
import json
import os
import statistics
import sys
from typing import Callable, Protocol

from optics_framework.common.logging_config import internal_logger
from optics_framework.helper.onboarding import optics_home

# A value at least this many times the median sharpness of the sweep is a focus peak, not noise.
PEAK_RATIO = 3.0
COARSE_POINTS = 25
NEAR_STEPS = 3
VIDEO, VIDEO_CONTROL = 14, 1
CS_INTERFACE, INPUT_TERMINAL, CAMERA_TERMINAL = 0x24, 0x02, 0x0201
FOCUS_ABSOLUTE, FOCUS_AUTO = 0x06, 0x08
FOCUS_ABSOLUTE_BIT, FOCUS_AUTO_BIT = 5, 17
SET_CUR, GET_CUR, GET_MIN, GET_MAX, GET_RES = 0x01, 0x81, 0x82, 0x83, 0x84
TO_INTERFACE, FROM_INTERFACE = 0x21, 0xA1
UTF8 = 0x08000100
SINT64 = 4
# Slots in IOUSBInterfaceStruct220 (IOUSBLib.h).
RELEASE, GET_INTERFACE_NUMBER, CONTROL_REQUEST, FIND_NEXT_ASSOCIATED_DESCRIPTOR = 3, 17, 24, 56
QUERY_INTERFACE = 1
USER_CLIENT = bytes.fromhex("2d9786c69ef311d4ad51000a27052861")
PLUGIN = bytes.fromhex("c244e858109c11d491d40050e4c6426f")
INTERFACE_220 = bytes.fromhex("770de60c2fe811d8a582000393dcb1d0")


class Lens(Protocol):
    span: range
    key: str

    def focus(self, value: int | None) -> None:
        """Hold the lens at ``value``; ``None`` gives focus back to the camera."""


def sharpest(lens: Lens, sharpness: Callable[[], float], near: int | None = None) -> int | None:
    """Hold the lens where ``sharpness`` peaks, looking ``near`` a value first; ``None``, with autofocus back on, when no value stands out of the blur."""
    span = lens.span
    if near is not None:
        if _holds(lens, sharpness, near):
            return near
        reach = NEAR_STEPS * _coarse(span)
        found = _peak(lens, sharpness, range(max(span.start, near - reach), min(span.stop, near + reach + 1), span.step))
        if found is not None:
            return found
    found = _peak(lens, sharpness, span)
    if found is None:
        internal_logger.warning("No focus value stands out of the blur; the camera keeps its autofocus")
        lens.focus(None)
    return found


def _holds(lens: Lens, sharpness: Callable[[], float], value: int) -> bool:
    """Whether ``value`` is still a focus peak: PEAK_RATIO times sharper than one coarse step on each side of it."""
    step = _coarse(lens.span)
    if value not in lens.span:
        return False
    scores = {}
    for at in (value - step, value, value + step):
        if at in lens.span:
            lens.focus(at)
            scores[at] = sharpness()
    lens.focus(value)
    sides = [score for at, score in scores.items() if at != value]
    return bool(sides) and scores[value] > PEAK_RATIO * max(sides)


def saved_focus(lens: Lens) -> int | None:
    """The focus value last held for this camera model, so a new session can confirm it instead of sweeping the lens."""
    try:
        with open(_focus_file(), encoding="utf-8") as file:
            value = json.load(file).get(lens.key)
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, int) else None


def save_focus(lens: Lens, value: int) -> None:
    path = _focus_file()
    try:
        with open(path, encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict) or saved.get(lens.key) == value:
        return
    saved[lens.key] = value
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(saved, file)
    except OSError as exc:
        internal_logger.info(f"Cannot save the camera's focus to {path}: {exc}")


def _focus_file() -> str:
    return os.path.join(optics_home(), "camera_focus.json")


def _coarse(span: range) -> int:
    return max(span.step, (span.stop - span.start) // COARSE_POINTS // span.step * span.step)


def _peak(lens: Lens, sharpness: Callable[[], float], span: range) -> int | None:
    step = _coarse(span)
    seen: dict[int, float] = {}

    def look(value: int) -> float:
        if value not in seen:
            lens.focus(value)
            seen[value] = sharpness()
        return seen[value]

    best = max(range(span.start, span.stop, step), key=look)
    while step > span.step:
        step = max(span.step, step // 2 // span.step * span.step)
        best = max((value for value in (best - step, best, best + step) if value in span), key=look)
    if look(best) <= PEAK_RATIO * statistics.median(seen.values()):
        return None
    lens.focus(best)
    return best


def uvc_lens() -> Lens | None:
    """The one UVC camera on this Mac whose focus can be set; ``None`` when there is none, or more than one."""
    if sys.platform != "darwin":
        return None
    try:
        lenses = _MacUvc.all()
    except OSError as exc:
        internal_logger.info(f"Cannot read the UVC cameras: {exc}")
        return None
    if len(lenses) != 1:
        if lenses:
            internal_logger.warning(f"{len(lenses)} UVC cameras can focus; Optics cannot tell which one films the device, so each keeps its autofocus")
        return None
    return lenses[0]


class _Request(ctypes.Structure):
    _fields_ = [
        ("bmRequestType", ctypes.c_uint8),
        ("bRequest", ctypes.c_uint8),
        ("wValue", ctypes.c_uint16),
        ("wIndex", ctypes.c_uint16),
        ("wLength", ctypes.c_uint16),
        ("pData", ctypes.c_void_p),
        ("wLenDone", ctypes.c_uint32),
    ]


class _UuidBytes(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_uint8 * 16)]


def _method(obj: ctypes.c_void_p, slot: int, restype, *argtypes) -> Callable:
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[slot])
    return lambda *args: function(obj, *args)


class _IOKit:
    def __init__(self) -> None:
        self.io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.io.IOServiceMatching.restype = ctypes.c_void_p
        self.io.IOServiceGetMatchingServices.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.io.IOIteratorNext.argtypes = [ctypes.c_uint32]
        self.io.IOIteratorNext.restype = ctypes.c_uint32
        self.io.IOObjectRelease.argtypes = [ctypes.c_uint32]
        self.io.IORegistryEntryCreateCFProperty.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        self.io.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
        self.io.IOCreatePlugInInterfaceForService.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int32)
        ]
        self.cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self.cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self.cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cf.CFUUIDGetConstantUUIDWithBytes.argtypes = [ctypes.c_void_p] + [ctypes.c_uint8] * 16
        self.cf.CFUUIDGetConstantUUIDWithBytes.restype = ctypes.c_void_p

    def services(self, kind: bytes) -> list[int]:
        iterator = ctypes.c_uint32()
        if self.io.IOServiceGetMatchingServices(0, self.io.IOServiceMatching(kind), ctypes.byref(iterator)):
            raise OSError(f"cannot list {kind.decode()}")
        found = []
        while service := self.io.IOIteratorNext(iterator):
            found.append(service)
        self.io.IOObjectRelease(iterator)
        return found

    def number(self, service: int, key: str) -> int | None:
        name = self.cf.CFStringCreateWithCString(None, key.encode(), UTF8)
        value = self.io.IORegistryEntryCreateCFProperty(service, name, None, 0)
        self.cf.CFRelease(name)
        if not value:
            return None
        out = ctypes.c_int64()
        self.cf.CFNumberGetValue(value, SINT64, ctypes.byref(out))
        self.cf.CFRelease(value)
        return out.value

    def interface(self, service: int) -> ctypes.c_void_p:
        plugin, score = ctypes.c_void_p(), ctypes.c_int32()
        user_client = self.cf.CFUUIDGetConstantUUIDWithBytes(None, *USER_CLIENT)
        plugin_id = self.cf.CFUUIDGetConstantUUIDWithBytes(None, *PLUGIN)
        if self.io.IOCreatePlugInInterfaceForService(service, user_client, plugin_id, ctypes.byref(plugin), ctypes.byref(score)) or not plugin:
            raise OSError("cannot reach the USB interface")
        found = ctypes.c_void_p()
        query = _method(plugin, QUERY_INTERFACE, ctypes.c_int32, _UuidBytes, ctypes.POINTER(ctypes.c_void_p))
        failed = query(_UuidBytes((ctypes.c_uint8 * 16)(*INTERFACE_220)), ctypes.byref(found))
        _method(plugin, RELEASE, ctypes.c_uint32)()
        if failed or not found:
            raise OSError("the USB interface has no control requests")
        return found


class _MacUvc:
    """Focus by UVC control requests on the camera's video control interface; the camera's driver may keep it open."""

    def __init__(self, interface: ctypes.c_void_p, key: str) -> None:
        self._interface = interface
        self.key = key
        self._release = _method(interface, RELEASE, ctypes.c_uint32)
        self._control = _method(interface, CONTROL_REQUEST, ctypes.c_int32, ctypes.c_uint8, ctypes.POINTER(_Request))
        number = ctypes.c_uint8()
        _method(interface, GET_INTERFACE_NUMBER, ctypes.c_int32, ctypes.POINTER(ctypes.c_uint8))(ctypes.byref(number))
        self._number = number.value
        self._terminal, self.can_focus = self._camera_terminal()
        self.span = range(0)
        self._manual = False
        self._auto_before: int | None = None
        if self.can_focus:
            low, high, step = (self._get(kind, FOCUS_ABSOLUTE, 2) for kind in (GET_MIN, GET_MAX, GET_RES))
            self.span = range(low, high + 1, max(step, 1))

    @classmethod
    def all(cls) -> list["_MacUvc"]:
        kit = _IOKit()
        lenses = []
        for service in kit.services(b"IOUSBHostInterface"):
            try:
                if (kit.number(service, "bInterfaceClass"), kit.number(service, "bInterfaceSubClass")) == (VIDEO, VIDEO_CONTROL):
                    model = f"{kit.number(service, 'idVendor') or 0:04x}:{kit.number(service, 'idProduct') or 0:04x}"
                    lens = cls(kit.interface(service), model)
                    if lens.can_focus:
                        lenses.append(lens)
            except OSError as exc:
                internal_logger.info(f"Skipping a UVC camera: {exc}")
            finally:
                kit.io.IOObjectRelease(service)
        return lenses

    def _camera_terminal(self) -> tuple[int, bool]:
        find = _method(self._interface, FIND_NEXT_ASSOCIATED_DESCRIPTOR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint8)
        descriptor = find(None, CS_INTERFACE)
        while descriptor:
            head = ctypes.string_at(descriptor, 3)
            body = ctypes.string_at(descriptor, head[0])
            if body[2] == INPUT_TERMINAL and int.from_bytes(body[4:6], "little") == CAMERA_TERMINAL:
                controls = int.from_bytes(body[15:15 + body[14]], "little")
                wanted = (1 << FOCUS_ABSOLUTE_BIT) | (1 << FOCUS_AUTO_BIT)
                return body[3], controls & wanted == wanted
            descriptor = find(descriptor, CS_INTERFACE)
        return 0, False

    def _transfer(self, kind: int, request: int, selector: int, data: ctypes.Array) -> None:
        message = _Request(kind, request, selector << 8, (self._terminal << 8) | self._number, len(data), ctypes.addressof(data), 0)
        status = self._control(0, ctypes.byref(message))
        if status:
            raise OSError(f"UVC request {request:#04x} for control {selector:#04x} failed with {status & 0xFFFFFFFF:#010x}")

    def _get(self, request: int, selector: int, length: int) -> int:
        data = (ctypes.c_uint8 * length)()
        self._transfer(FROM_INTERFACE, request, selector, data)
        return int.from_bytes(bytes(data), "little")

    def _set(self, selector: int, value: int, length: int) -> None:
        self._transfer(TO_INTERFACE, SET_CUR, selector, (ctypes.c_uint8 * length)(*value.to_bytes(length, "little")))

    def focus(self, value: int | None) -> None:
        if value is None:
            self._set(FOCUS_AUTO, 1, 1)
            self._manual = False
            return
        if not self._manual:
            if self._auto_before is None:
                self._auto_before = self._get(GET_CUR, FOCUS_AUTO, 1)
            self._set(FOCUS_AUTO, 0, 1)
            self._manual = True
        self._set(FOCUS_ABSOLUTE, value, 2)

    def __del__(self) -> None:
        if not getattr(self, "_release", None):
            return
        if self._manual and self._auto_before is not None:
            try:
                self._set(FOCUS_AUTO, self._auto_before, 1)
            except OSError as exc:
                internal_logger.warning(f"The camera's focus mode could not be set back: {exc}")
        self._release()
