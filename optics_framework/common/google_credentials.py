from typing import TYPE_CHECKING

from optics_framework.common.error import Code, OpticsError

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials

_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def service_account_credentials(path: str) -> "Credentials":
    """Load a Google service account key file, or stop with E0501 naming the file."""
    from google.oauth2 import service_account

    try:
        return service_account.Credentials.from_service_account_file(path, scopes=[_CLOUD_PLATFORM_SCOPE])
    except (OSError, ValueError) as exc:
        raise OpticsError(
            Code.E0501, message=f"Cannot use {path} as a Google service account key: {exc}"
        ) from exc
