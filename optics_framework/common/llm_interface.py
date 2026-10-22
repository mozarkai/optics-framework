import json
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any

from optics_framework.common.error import OpticsError, Code


def missing_extra_error(engine: str, package: str, import_error: Exception | None) -> OpticsError:
    return OpticsError(
        Code.E0601,
        message=(
            f"The '{engine}' LLM engine requires the optional dependency '{package}'. "
            f"Install it with: pip install 'optics-framework[{engine}]' "
            f"(or: poetry install --extras {engine}). "
            f"Underlying import error: {import_error}"
        ),
    )


def image_mime_type(data: bytes) -> str:
    """Sniff a common image mime type from magic bytes.

    Callers may send PNG, JPEG, WebP or GIF, and providers need the real type.
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


class LLMInterface(ABC):
    """
    Abstract base class for multimodal LLM engines.

    The contract is deliberately generic (text prompt, optional images, optional
    structured-JSON output) so the same engine can be reused by features beyond the
    natural-language ``optics live`` agent. Implementers wrap a concrete provider SDK
    (``gemini``, ``openai``, ``anthropic`` under ``engines/llm_models``).
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        images: Optional[List[bytes]] = None,
        system: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        *,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Return the model's text response.

        :param prompt: The user prompt.
        :param images: Optional raw image bytes (PNG/JPEG). Kept as bytes (not base64 or
                       numpy) so the interface stays SDK-agnostic.
        :param system: Optional system instruction.
        :param response_schema: Optional JSON schema. When provided the engine forces JSON
                                output and the returned string is a JSON document.
        :param temperature: Optional sampling temperature override.
        :return: The model response text (a JSON document when ``response_schema`` is set).
        """
        raise NotImplementedError

    def generate_json(
        self,
        prompt: str,
        response_schema: Dict[str, Any],
        images: Optional[List[bytes]] = None,
        system: Optional[str] = None,
        *,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Convenience wrapper: call :meth:`generate` in JSON mode and parse the result.

        Concrete (non-abstract) so every engine inherits consistent JSON handling.

        :raises OpticsError: ``Code.E0801`` if the model returns undecodable JSON.
        """
        text = self.generate(
            prompt,
            images=images,
            system=system,
            response_schema=response_schema,
            temperature=temperature,
        )
        try:
            return json.loads(text)
        except (ValueError, TypeError) as exc:
            raise OpticsError(
                Code.E0801,
                message=f"LLM returned non-JSON output: {text[:200]}",
            ) from exc
