import base64
import json
import os
from typing import Any

from optics_framework.common.llm_interface import LLMInterface, image_mime_type, missing_extra_error
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code

try:
    import anthropic as anthropic_sdk

    _IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
    anthropic_sdk = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


_DEFAULT_MAX_TOKENS = 4096
_PLATFORMS = ("anthropic", "bedrock", "vertex")
_RESPONSE_TOOL = "respond"


class AnthropicLLM(LLMInterface):
    """
    Claude via the ``anthropic`` SDK, on the Claude API, Amazon Bedrock or Google Vertex AI.
    Capabilities are documented under "LLM Models" in ``docs/configuration.md``.

    Structured output is a forced call to a single tool whose input schema is the response
    schema. Forced tool use works on every platform and Claude model, which native JSON
    output does not yet.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        if anthropic_sdk is None:
            raise missing_extra_error("anthropic", "anthropic", _IMPORT_ERROR)

        config = config or {}
        caps: dict[str, Any] = config.get("capabilities") or {}
        model = caps.get("model")
        if not model:
            raise OpticsError(
                Code.E0601,
                message="The 'anthropic' LLM engine needs capabilities.model (e.g. claude-sonnet-5-5).",
            )
        self.model_name: str = model
        self.max_tokens: int = int(caps.get("max_tokens", _DEFAULT_MAX_TOKENS))
        self.temperature: float | None = caps.get("temperature")
        platform = caps.get("platform", "anthropic")
        if platform not in _PLATFORMS:
            raise OpticsError(
                Code.E0601,
                message=f"capabilities.platform must be one of {_PLATFORMS}, got '{platform}'.",
            )

        try:
            self.client = _build_client(platform, caps, config.get("url"))
        except Exception as exc:  # noqa: BLE001 - surface any client/auth init failure uniformly
            raise OpticsError(
                Code.E0601, message=f"Failed to initialize Anthropic client ({platform}): {exc}"
            ) from exc
        internal_logger.debug("AnthropicLLM initialized with model '%s' on %s", self.model_name, platform)

    def generate(
        self,
        prompt: str,
        images: list[bytes] | None = None,
        system: str | None = None,
        response_schema: dict[str, Any] | None = None,
        *,
        temperature: float | None = None,
    ) -> str:
        content: list[dict[str, Any]] = [_image_block(img) for img in images or []]
        content.append({"type": "text", "text": prompt})
        request: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            # The system prompt and tool are identical on every step of an agent run.
            request["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        effective_temperature = self.temperature if temperature is None else temperature
        if effective_temperature is not None:
            request["temperature"] = effective_temperature
        if response_schema is not None:
            request["tools"] = [{
                "name": _RESPONSE_TOOL,
                "description": "Return the response. Always call this tool exactly once.",
                "input_schema": response_schema,
            }]
            request["tool_choice"] = {"type": "tool", "name": _RESPONSE_TOOL}

        try:
            response = self.client.messages.create(**request)
        except Exception as exc:  # noqa: BLE001 - normalize all SDK/transport errors
            raise OpticsError(Code.E0801, message=f"Anthropic request failed: {exc}") from exc

        internal_logger.debug(
            "Anthropic usage (%s): input=%s cache_read=%s output=%s",
            self.model_name, response.usage.input_tokens,
            response.usage.cache_read_input_tokens, response.usage.output_tokens,
        )
        if response.stop_reason == "max_tokens":
            raise OpticsError(
                Code.E0801,
                message=f"Claude hit max_tokens ({self.max_tokens}); raise capabilities.max_tokens.",
            )
        if response_schema is None:
            return "".join(block.text for block in response.content if block.type == "text")
        for block in response.content:
            if block.type == "tool_use":
                return json.dumps(block.input)
        raise OpticsError(Code.E0801, message="Claude returned no structured response.")


def _build_client(platform: str, caps: dict[str, Any], base_url: str | None) -> Any:
    if platform == "bedrock":
        return anthropic_sdk.AnthropicBedrock(aws_region=caps.get("region"), base_url=base_url)
    if platform == "vertex":
        vertex_kwargs: dict[str, Any] = {"base_url": base_url}
        if caps.get("region"):
            vertex_kwargs["region"] = caps["region"]
        if caps.get("project"):
            vertex_kwargs["project_id"] = caps["project"]
        return anthropic_sdk.AnthropicVertex(**vertex_kwargs)
    api_key = os.getenv(caps.get("api_key_env", "ANTHROPIC_API_KEY"))
    return anthropic_sdk.Anthropic(api_key=api_key, base_url=base_url)


def _image_block(data: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image_mime_type(data),
            "data": base64.b64encode(data).decode("ascii"),
        },
    }
