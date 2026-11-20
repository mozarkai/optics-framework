"""The ``permissions`` config field: short names for the Android permissions a session sets before the app starts."""
from typing import Annotated, Dict, Literal

from pydantic import AfterValidator

from optics_framework.common.error import Code, OpticsError

# What each permission name in the session config stands for on Android; a full "android.permission.X" is taken as it is.
# The strings are listed at https://developer.android.com/reference/android/Manifest.permission
ANDROID_PERMISSIONS = {
    "camera": ("android.permission.CAMERA",),
    "location": ("android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION"),
    "notifications": ("android.permission.POST_NOTIFICATIONS",),
    "microphone": ("android.permission.RECORD_AUDIO",),
    "background_location": ("android.permission.ACCESS_BACKGROUND_LOCATION",),
    "contacts": ("android.permission.READ_CONTACTS", "android.permission.WRITE_CONTACTS"),
    "calendar": ("android.permission.READ_CALENDAR", "android.permission.WRITE_CALENDAR"),
    "phone": ("android.permission.READ_PHONE_STATE", "android.permission.CALL_PHONE"),
    "call_log": ("android.permission.READ_CALL_LOG", "android.permission.WRITE_CALL_LOG"),
    "sms": ("android.permission.SEND_SMS", "android.permission.READ_SMS", "android.permission.RECEIVE_SMS"),
    "storage": ("android.permission.READ_EXTERNAL_STORAGE", "android.permission.WRITE_EXTERNAL_STORAGE"),
    "photos": ("android.permission.READ_MEDIA_IMAGES",),
    "videos": ("android.permission.READ_MEDIA_VIDEO",),
    "audio": ("android.permission.READ_MEDIA_AUDIO",),
    "bluetooth": ("android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_SCAN",
                  "android.permission.BLUETOOTH_ADVERTISE"),
    "nearby_wifi": ("android.permission.NEARBY_WIFI_DEVICES",),
    "body_sensors": ("android.permission.BODY_SENSORS",),
    "activity_recognition": ("android.permission.ACTIVITY_RECOGNITION",),
}


def android_permissions(name: str) -> tuple[str, ...]:
    """The Android permissions ``name`` stands for; an unknown name raises E0501."""
    if name.startswith("android.permission."):
        return (name,)
    try:
        return ANDROID_PERMISSIONS[name.lower()]
    except KeyError:
        raise OpticsError(
            Code.E0501,
            message=f"Unknown permission '{name}'. Use one of {', '.join(ANDROID_PERMISSIONS)} or a full android.permission.* name.",
        ) from None


def _known(name: str) -> str:
    android_permissions(name)
    return name


Permissions = Dict[Annotated[str, AfterValidator(_known)], Literal["allow", "deny"]]
