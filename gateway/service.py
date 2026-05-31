from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from gateway.config import GatewaySettings
from gateway.schemas import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    ChatMessage,
    ModelCard,
    ModelListResponse,
)

if TYPE_CHECKING:
    from gemini_webapi import GeminiClient


@dataclass(frozen=True)
class GatewayModelSpec:
    canonical_id: str
    upstream_name: str


class GatewayServiceError(Exception):
    def __init__(self, message: str, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


CANONICAL_MODELS: tuple[GatewayModelSpec, ...] = (
    GatewayModelSpec("gemini-3.1-pro", "gemini-3-pro"),
    GatewayModelSpec("gemini-3.5-flash", "gemini-3-flash"),
    GatewayModelSpec("gemini-3.1-flash-lite", "3.1 Flash-Lite"),
)

MODEL_ALIASES: dict[str, str] = {
    "gemini-3.1-pro": "gemini-3.1-pro",
    "gemini-3-pro": "gemini-3.1-pro",
    "3.1-pro": "gemini-3.1-pro",
    "gemini-3.5-flash": "gemini-3.5-flash",
    "gemini-3-flash": "gemini-3.5-flash",
    "3.5-flash": "gemini-3.5-flash",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "3.1-flash-lite": "gemini-3.1-flash-lite",
    "3.1 Flash-Lite": "gemini-3.1-flash-lite",
}


class GatewayService:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._models_by_id = {
            model.canonical_id: model
            for model in CANONICAL_MODELS
        }

    def list_models(self) -> ModelListResponse:
        return ModelListResponse(
            data=[ModelCard(id=model.canonical_id) for model in CANONICAL_MODELS]
        )

    def resolve_model(self, public_name: str | None) -> GatewayModelSpec:
        requested_name = public_name or self.settings.default_model
        canonical_name = MODEL_ALIASES.get(requested_name, requested_name)
        model = self._models_by_id.get(canonical_name)
        if model is None:
            raise GatewayServiceError(
                message=f"Unsupported model: {requested_name}",
                code="invalid_model",
                status_code=400,
            )
        return model

    def resolve_reasoning_effort(self, request: ChatCompletionRequest) -> str:
        extra_body = request.extra_body or {}
        return (
            request.reasoning_effort
            or extra_body.get("reasoning_effort")
            or self.settings.default_reasoning_effort
        )

    def load_cookies(self) -> dict[str, str]:
        cookies_path = Path(self.settings.cookies_json_path)
        if not cookies_path.exists():
            raise GatewayServiceError(
                message=f"Cookies file not found: {cookies_path}",
                code="missing_cookies",
                status_code=500,
            )

        try:
            raw_data = json.loads(cookies_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GatewayServiceError(
                message=f"Cookies JSON is invalid: {cookies_path}",
                code="missing_cookies",
                status_code=500,
            ) from exc

        cookies = self._extract_cookies(raw_data)
        if "__Secure-1PSID" not in cookies:
            raise GatewayServiceError(
                message="Missing __Secure-1PSID in cookies JSON.",
                code="missing_cookies",
                status_code=500,
            )
        return cookies

    def _extract_cookies(self, raw_data: Any) -> dict[str, str]:
        if isinstance(raw_data, dict) and isinstance(raw_data.get("cookies"), dict):
            return {
                name: value
                for name, value in raw_data["cookies"].items()
                if isinstance(name, str) and isinstance(value, str)
            }

        if isinstance(raw_data, dict) and isinstance(raw_data.get("cookies"), list):
            return self._cookies_from_list(raw_data["cookies"])

        if isinstance(raw_data, list):
            return self._cookies_from_list(raw_data)

        if isinstance(raw_data, dict):
            return {
                name: value
                for name, value in raw_data.items()
                if isinstance(name, str) and isinstance(value, str)
            }

        raise GatewayServiceError(
            message="Unsupported cookies JSON format.",
            code="missing_cookies",
            status_code=500,
        )

    def _cookies_from_list(self, cookie_items: list[Any]) -> dict[str, str]:
        cookies: dict[str, str] = {}
        for item in cookie_items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                cookies[name] = value
        return cookies

    def build_prompt_from_messages(self, messages: list[ChatMessage]) -> str:
        sections: list[str] = []
        system_messages: list[str] = []

        for message in messages:
            text = self._extract_text_content(message.content)
            if not text:
                continue

            if message.role == "system":
                system_messages.append(text)
                continue

            role_label = message.role.capitalize()
            if message.role == "tool" and message.tool_call_id:
                role_label = f"Tool[{message.tool_call_id}]"
            sections.append(f"{role_label}:\n{text}")

        prompt_parts: list[str] = []
        if system_messages:
            prompt_parts.append("System:\n" + "\n\n".join(system_messages))
        prompt_parts.extend(sections)
        prompt_parts.append("Assistant:")
        return "\n\n".join(prompt_parts)

    def _extract_text_content(self, content: str | list[Any] | None) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            raise GatewayServiceError(
                message="Unsupported message content type.",
                code="invalid_request",
                status_code=400,
            )

        text_parts: list[str] = []
        for part in content:
            part_type = getattr(part, "type", None)
            part_text = getattr(part, "text", None)
            if part_type == "text" and isinstance(part_text, str):
                text_parts.append(part_text)
                continue

            raise GatewayServiceError(
                message="Only text message parts are supported for now.",
                code="invalid_request",
                status_code=400,
            )

        return "\n".join(text_parts).strip()

    async def create_chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if request.stream:
            raise GatewayServiceError(
                message="stream=true is not supported yet.",
                code="unsupported_stream",
                status_code=400,
            )

        resolved_model = self.resolve_model(request.model)
        prompt = self.build_prompt_from_messages(request.messages)
        _ = self.resolve_reasoning_effort(request)
        response_text = await self.generate_text(
            prompt=prompt,
            upstream_model=resolved_model.upstream_name,
            request=request,
        )

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid4().hex}",
            created=int(time.time()),
            model=resolved_model.canonical_id,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionResponseMessage(
                        role="assistant",
                        content=response_text,
                    ),
                    finish_reason="stop",
                )
            ],
        )

    async def generate_text(
        self,
        prompt: str,
        upstream_model: str,
        request: ChatCompletionRequest,
    ) -> str:
        client = self.build_gemini_client()
        try:
            await client.init(
                timeout=self.settings.request_timeout,
                auto_refresh=False,
                auto_close=False,
            )
            response = await client.generate_content(
                prompt=prompt,
                model=upstream_model,
            )
            return response.text
        finally:
            await client.close()

    def build_gemini_client(self) -> GeminiClient:
        from gemini_webapi import GeminiClient

        cookies = self.load_cookies()
        psid = cookies.get("__Secure-1PSID")
        psidts = cookies.get("__Secure-1PSIDTS", "")
        extra_cookies = {
            name: value
            for name, value in cookies.items()
            if name not in {"__Secure-1PSID", "__Secure-1PSIDTS"}
        }
        return GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            cookies=extra_cookies or None,
            proxy=self.settings.proxy,
        )
