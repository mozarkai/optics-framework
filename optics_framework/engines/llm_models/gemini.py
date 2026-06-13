from typing import Optional, List, Dict, Any

from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code

# The google-genai SDK is an optional extra (``optics-framework[llm]``). Import is guarded
# so merely importing this module never fails when the extra is absent; the actionable error
# is raised on instantiation, i.e. only when a 'gemini' engine is actually enabled in config.
try:
    from google import genai
    from google.genai import types as genai_types

    _IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


_DEFAULT_MODEL = "gemini-2.5-flash"


def _image_mime_type(data: bytes) -> str:
    """Sniff a common image mime type from magic bytes.

    The interface passes raw image bytes (callers may send PNG, JPEG, WebP, GIF);
    we detect rather than assume so the engine isn't tied to one encoding.
    Raises ``ValueError`` when the format cannot be identified.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError(
        f"Unrecognised image format (first 8 bytes: {data[:8]!r}). "
        "Supported formats: PNG, JPEG, GIF, WebP."
    )


class GeminiLLM(LLMInterface):
    """
    Google Gemini engine (``google-genai`` SDK), supporting both the Gemini Developer API
    and Vertex AI. With no explicit ``capabilities`` the SDK auto-selects the backend and
    credentials from the environment (``GOOGLE_GENAI_USE_VERTEXAI``,
    ``GOOGLE_API_KEY`` / ``GEMINI_API_KEY``, ``GOOGLE_CLOUD_PROJECT``,
    ``GOOGLE_CLOUD_LOCATION``, ``GOOGLE_APPLICATION_CREDENTIALS``). Capabilities override env
    when present; secrets are never hardcoded.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if genai is None:
            raise OpticsError(
                Code.E0601,
                message=(
                    "The 'gemini' LLM engine requires the optional dependency 'google-genai'. "
                    "Install it with: pip install 'optics-framework[llm]' "
                    "(or: poetry install --extras llm). "
                    f"Underlying import error: {_IMPORT_ERROR}"
                ),
            )

        caps: Dict[str, Any] = (config or {}).get("capabilities") or {}
        self.model_name: str = caps.get("model", _DEFAULT_MODEL)
        self.temperature: float = caps.get("temperature", 0.0)

        client_kwargs: Dict[str, Any] = {}
        # Explicit overrides win over env vars; absent kwargs fall back to SDK env auto-config.
        if "use_vertexai" in caps:
            client_kwargs["vertexai"] = bool(caps["use_vertexai"])
        api_key = caps.get("api_key") or caps.get("gemini_api_key")
        if api_key:
            client_kwargs["api_key"] = api_key
            internal_logger.warning(
                "GeminiLLM: an API key was read from the config 'capabilities'. Storing "
                "secrets in a plaintext config is insecure — prefer the GOOGLE_API_KEY / "
                "GEMINI_API_KEY environment variables, and never commit a config with a key."
            )
        if caps.get("project"):
            client_kwargs["project"] = caps["project"]
        if caps.get("location"):
            client_kwargs["location"] = caps["location"]

        try:
            self.client = genai.Client(**client_kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any client/auth init failure uniformly
            raise OpticsError(
                Code.E0601, message=f"Failed to initialize Gemini client: {exc}"
            ) from exc
        internal_logger.debug("GeminiLLM initialized with model '%s'", self.model_name)

    def generate(
        self,
        prompt: str,
        images: Optional[List[bytes]] = None,
        system: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        *,
        temperature: Optional[float] = None,
    ) -> str:
        contents: List[Any] = [prompt]
        if images:
            for img in images:
                contents.append(
                    genai_types.Part.from_bytes(data=img, mime_type=_image_mime_type(img))
                )

        config_kwargs: Dict[str, Any] = {
            "temperature": self.temperature if temperature is None else temperature,
        }
        if system:
            config_kwargs["system_instruction"] = system
        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema
        gen_config = genai_types.GenerateContentConfig(**config_kwargs)

        try:
            response = self.client.models.generate_content(
                model=self.model_name, contents=contents, config=gen_config
            )
        except Exception as exc:  # noqa: BLE001 - normalize all SDK/transport errors
            raise OpticsError(
                Code.E0801, message=f"Gemini request failed: {exc}"
            ) from exc
        return response.text or ""
