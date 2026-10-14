import base64
import json
import os
from typing import Any

from optics_framework.common.llm_interface import LLMInterface, image_mime_type, missing_extra_error
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code

try:
    from openai import OpenAI

    _IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
    OpenAI = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = exc


_DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
_RESPONSE_FORMATS = ("json_schema", "json_object")
# Local servers (Ollama, vLLM, LM Studio) ignore the key, but the SDK refuses to start without one.
_KEYLESS_PLACEHOLDER = "unused"


class OpenAILLM(LLMInterface):
    """
    Any OpenAI-compatible Chat Completions endpoint, via the ``openai`` SDK.

    Without a ``url`` (or ``OPENAI_BASE_URL``) this talks to OpenAI; with one, to whatever
    serves the protocol there: OpenRouter, Azure OpenAI, Ollama, vLLM, LM Studio, Groq.
    Capabilities are documented under "LLM Models" in ``docs/configuration.md``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        if OpenAI is None:
            raise missing_extra_error("openai", "openai", _IMPORT_ERROR)

        config = config or {}
        caps: dict[str, Any] = config.get("capabilities") or {}
        model = caps.get("model")
        if not model:
            raise OpticsError(
                Code.E0601,
                message="The 'openai' LLM engine needs capabilities.model, as the server at "
                "its url names it (e.g. anthropic/claude-sonnet-5-5 on OpenRouter).",
            )
        self.model_name: str = model
        self.temperature: float | None = caps.get("temperature")
        self.max_tokens: int | None = caps.get("max_tokens")
        self.response_format: str = caps.get("response_format", "json_schema")
        if self.response_format not in _RESPONSE_FORMATS:
            raise OpticsError(
                Code.E0601,
                message=f"capabilities.response_format must be one of {_RESPONSE_FORMATS}, "
                f"got '{self.response_format}'.",
            )

        base_url = config.get("url") or os.getenv("OPENAI_BASE_URL")
        api_key_env = caps.get("api_key_env", _DEFAULT_API_KEY_ENV)
        api_key = os.getenv(api_key_env)
        if not api_key and not base_url:
            raise OpticsError(
                Code.E0601,
                message=f"The 'openai' LLM engine found no API key: set {api_key_env}.",
            )

        try:
            self.client = OpenAI(api_key=api_key or _KEYLESS_PLACEHOLDER, base_url=base_url)
        except Exception as exc:  # noqa: BLE001 - surface any client init failure uniformly
            raise OpticsError(
                Code.E0601, message=f"Failed to initialize OpenAI client: {exc}"
            ) from exc
        internal_logger.debug(
            "OpenAILLM initialized with model '%s' at %s", self.model_name, base_url or "api.openai.com"
        )

    def generate(
        self,
        prompt: str,
        images: list[bytes] | None = None,
        system: str | None = None,
        response_schema: dict[str, Any] | None = None,
        *,
        temperature: float | None = None,
    ) -> str:
        request, response_format = self._build_request(prompt, images, system, response_schema, temperature)
        try:
            response = self.client.chat.completions.create(**request)
        except Exception as exc:  # noqa: BLE001 - normalize all SDK/transport errors
            raise OpticsError(
                Code.E0801, message=f"OpenAI-compatible request failed: {exc}"
            ) from exc
        return self._read_reply(response, response_format, response_schema)

    def _build_request(
        self,
        prompt: str,
        images: list[bytes] | None,
        system: str | None,
        response_schema: dict[str, Any] | None,
        temperature: float | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        response_format: dict[str, Any] | None = None
        if response_schema is not None and self.response_format == "json_object":
            system = _with_schema_instruction(system, response_schema)
            response_format = {"type": "json_object"}
        elif response_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": strict_schema(response_schema), "strict": True},
            }

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": _user_content(prompt, images)})

        request: dict[str, Any] = {"model": self.model_name, "messages": messages}
        effective_temperature = self.temperature if temperature is None else temperature
        if effective_temperature is not None:
            request["temperature"] = effective_temperature
        if self.max_tokens is not None:
            request["max_completion_tokens"] = self.max_tokens
        if response_format is not None:
            request["response_format"] = response_format
        return request, response_format

    def _read_reply(
        self,
        response: Any,
        response_format: dict[str, Any] | None,
        response_schema: dict[str, Any] | None,
    ) -> str:
        if response.usage is not None:
            internal_logger.debug(
                "OpenAI usage (%s): prompt=%s output=%s",
                self.model_name, response.usage.prompt_tokens, response.usage.completion_tokens,
            )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise OpticsError(
                Code.E0801,
                message="The model hit its output limit before finishing; raise capabilities.max_tokens.",
            )
        message = choice.message
        if message.refusal:
            raise OpticsError(Code.E0801, message=f"Model refused the request: {message.refusal}")
        content = message.content or ""
        if response_format is not None and response_format["type"] == "json_schema":
            try:
                content = json.dumps(_drop_added_nulls(json.loads(content), response_schema))
            except ValueError:
                pass  # generate_json reports the undecodable reply
        return content


def _user_content(prompt: str, images: list[bytes] | None) -> str | list[dict[str, Any]]:
    # Plain-string content when there are no images: some compatible servers reject parts arrays.
    if not images:
        return prompt
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in images:
        data_url = f"data:{image_mime_type(img)};base64,{base64.b64encode(img).decode('ascii')}"
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


def _with_schema_instruction(system: str | None, schema: dict[str, Any]) -> str:
    instruction = (
        "Reply with a single JSON object that conforms to this JSON schema:\n" + json.dumps(schema)
    )
    return f"{system}\n\n{instruction}" if system else instruction


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a JSON schema into the form OpenAI's strict mode accepts.

    Strict mode requires every object to be closed (``additionalProperties: false``) and to
    list every property in ``required``. A property that was optional becomes nullable
    instead, which is how strict mode expresses "may be absent".
    """
    out = dict(schema)
    if isinstance(out.get("items"), dict):
        out["items"] = strict_schema(out["items"])
    properties = out.get("properties")
    if isinstance(properties, dict):
        required = set(out.get("required", []))
        out["properties"] = {
            name: strict_schema(sub) if name in required else _nullable(strict_schema(sub))
            for name, sub in properties.items()
        }
        out["required"] = list(properties)
        out["additionalProperties"] = False
    return out


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    if isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    if "enum" in out and None not in out["enum"]:
        out["enum"] = [*out["enum"], None]
    return out


def _drop_added_nulls(value: Any, schema: dict[str, Any]) -> Any:
    """Omit the optional properties that strict mode returned as null, as other engines do."""
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_drop_added_nulls(item, schema["items"]) for item in value]
    properties = schema.get("properties")
    if not isinstance(value, dict) or not isinstance(properties, dict):
        return value
    required = set(schema.get("required", []))
    return {
        key: _drop_added_nulls(item, properties.get(key, {}))
        for key, item in value.items()
        if item is not None or key in required
    }
