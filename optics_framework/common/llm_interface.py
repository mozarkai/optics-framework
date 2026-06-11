from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any

from optics_framework.common.error import OpticsError, Code


class LLMInterface(ABC):
    """
    Abstract base class for multimodal LLM engines.

    The contract is deliberately generic (text prompt, optional images, optional
    structured-JSON output) so the same engine can be reused by features beyond the
    natural-language ``optics live`` agent. Implementers wrap a concrete provider SDK
    (e.g. Google Gemini via ``google-genai``).
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
        import json

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
