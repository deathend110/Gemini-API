from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, AsyncGenerator, TYPE_CHECKING
from urllib.error import HTTPError, URLError
from uuid import uuid4

from gateway.config import GatewaySettings
from gateway.files import cleanup_prepared_files, prepare_request_files
from gateway.schemas import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    ChatMessage,
    ModelCard,
    ModelListResponse,
    ChatToolCall,
    ChatToolFunction,
)
from gateway.streaming import build_chunk, format_sse
from gemini_webapi.exceptions import APIError, AuthError, GeminiError, TimeoutError

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
        effort = (
            request.reasoning_effort
            or extra_body.get("reasoning_effort")
            or self.settings.default_reasoning_effort
        )
        if effort not in {"standard", "extended"}:
            raise GatewayServiceError(
                message=f"Unsupported reasoning_effort: {effort}",
                code="invalid_reasoning_effort",
                status_code=400,
            )
        return effort

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

    def build_prompt_from_messages(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        reasoning_effort: str = "standard",
    ) -> str:
        sections: list[str] = []
        system_messages: list[str] = []
        tool_name_by_call_id: dict[str, str] = {}

        for message in messages:
            text = self._extract_text_content(message.content)

            if message.role == "system":
                if text:
                    system_messages.append(text)
                continue

            if message.role == "assistant":
                assistant_blocks: list[str] = []
                if text:
                    assistant_blocks.append(text)
                if message.tool_calls:
                    serialized_tool_calls: list[dict[str, Any]] = []
                    for tool_call in message.tool_calls:
                        tool_name_by_call_id[tool_call.id] = tool_call.function.name
                        serialized_tool_calls.append(
                            {
                                "id": tool_call.id,
                                "name": tool_call.function.name,
                                "arguments": self._normalize_tool_arguments(
                                    tool_call.function.arguments
                                ),
                            }
                        )
                    assistant_blocks.append(
                        "Tool calls:\n"
                        + json.dumps(
                            {"tool_calls": serialized_tool_calls},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                if assistant_blocks:
                    sections.append("Assistant:\n" + "\n\n".join(assistant_blocks))
                continue

            if not text:
                continue

            role_label = message.role.capitalize()
            if message.role == "tool" and message.tool_call_id:
                tool_name = message.name or tool_name_by_call_id.get(message.tool_call_id)
                role_label = f"Tool[{message.tool_call_id}]"
                if tool_name:
                    role_label = f"Tool[{tool_name}][{message.tool_call_id}]"
            sections.append(f"{role_label}:\n{text}")

        prompt_parts: list[str] = []
        if system_messages:
            prompt_parts.append("System:\n" + "\n\n".join(system_messages))
        if reasoning_effort == "extended":
            prompt_parts.append(
                "Reasoning:\nUse extended reasoning. Think step by step internally before answering."
            )

        tool_instructions = self.build_tool_instructions(tools, tool_choice)
        if tool_instructions:
            prompt_parts.append("Tooling:\n" + tool_instructions)

        prompt_parts.extend(sections)
        prompt_parts.append("Assistant:")
        return "\n\n".join(prompt_parts)

    def build_tool_instructions(
        self,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> str:
        if not tools:
            return ""

        lines = [
            "You may use tools if they are helpful.",
            'If you decide to call tools, reply with JSON only in this exact shape: {"tool_calls":[{"name":"tool_name","arguments":{"key":"value"}}]}',
            "Do not wrap tool JSON in markdown fences.",
            "Available tools:",
        ]

        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function_spec = tool.get("function")
            if not isinstance(function_spec, dict):
                continue
            lines.append(
                json.dumps(function_spec, ensure_ascii=False, separators=(",", ":"))
            )

        if tool_choice == "none":
            lines.append("Tool choice is none. Do not call any tools.")
        elif tool_choice == "required":
            lines.append("Tool choice is required. You must call at least one tool.")
        elif isinstance(tool_choice, dict):
            function_spec = tool_choice.get("function")
            if isinstance(function_spec, dict) and isinstance(
                function_spec.get("name"), str
            ):
                lines.append(
                    f"Tool choice is fixed. You must call the tool named {function_spec['name']}."
                )

        return "\n".join(lines)

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
            if part_type == "image_url":
                text_parts.append("[Image input attached]")
                continue

            raise GatewayServiceError(
                message="Unsupported message content part.",
                code="invalid_request",
                status_code=400,
            )

        return "\n".join(text_parts).strip()

    def parse_tool_calls(
        self,
        response_text: str,
    ) -> list[ChatToolCall] | None:
        parsed_payload = self._parse_json_payload(response_text)
        if not isinstance(parsed_payload, dict):
            return None

        raw_tool_calls = parsed_payload.get("tool_calls")
        if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
            return None

        tool_calls: list[ChatToolCall] = []
        for index, raw_tool_call in enumerate(raw_tool_calls):
            if not isinstance(raw_tool_call, dict):
                return None

            function_name = raw_tool_call.get("name")
            arguments = raw_tool_call.get("arguments")

            if not isinstance(function_name, str):
                function_block = raw_tool_call.get("function")
                if isinstance(function_block, dict):
                    function_name = function_block.get("name")
                    arguments = function_block.get("arguments", arguments)

            if not isinstance(function_name, str):
                return None

            normalized_arguments = self._normalize_tool_arguments(arguments)
            if normalized_arguments is None:
                return None

            tool_calls.append(
                ChatToolCall(
                    id=f"call_{uuid4().hex[:24]}_{index}",
                    function=ChatToolFunction(
                        name=function_name,
                        arguments=normalized_arguments,
                    ),
                )
            )

        return tool_calls

    def _normalize_tool_arguments(self, arguments: Any) -> str | None:
        if isinstance(arguments, str):
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed_arguments, dict):
                return None
            return json.dumps(
                parsed_arguments,
                ensure_ascii=False,
                separators=(",", ":"),
            )

        if arguments is None:
            normalized = {}
        else:
            normalized = arguments

        if not isinstance(normalized, dict):
            return None

        return json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _parse_json_payload(self, response_text: str) -> Any:
        candidate = response_text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                candidate = "\n".join(lines[1:-1]).strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    async def create_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        files: list[Path] = []
        try:
            resolved_model = self.resolve_model(request.model)
            reasoning_effort = self.resolve_reasoning_effort(request)
            prompt = self.build_prompt_from_messages(
                request.messages,
                tools=request.tools,
                tool_choice=request.tool_choice,
                reasoning_effort=reasoning_effort,
            )
            files = await prepare_request_files(
                request.messages,
                request.extra_body,
                self.settings.request_timeout,
                self.settings.proxy,
            )
            response_text = await self.generate_text(
                prompt=prompt,
                upstream_model=resolved_model.upstream_name,
                request=request,
                files=files or None,
            )
        except Exception as exc:
            raise self._normalize_exception(exc) from exc
        finally:
            cleanup_prepared_files(files)

        tool_calls = self.parse_tool_calls(response_text) if request.tools else None
        finish_reason = "tool_calls" if tool_calls else "stop"
        content = "" if tool_calls else response_text

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid4().hex}",
            created=int(time.time()),
            model=resolved_model.canonical_id,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionResponseMessage(
                        role="assistant",
                        content=content,
                        tool_calls=tool_calls,
                    ),
                    finish_reason=finish_reason,
                )
            ],
        )

    async def stream_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncGenerator[str, None]:
        created = int(time.time())
        response_id = f"chatcmpl-{uuid4().hex}"
        files: list[Path] = []

        try:
            resolved_model = self.resolve_model(request.model)
            reasoning_effort = self.resolve_reasoning_effort(request)
            prompt = self.build_prompt_from_messages(
                request.messages,
                tools=request.tools,
                tool_choice=request.tool_choice,
                reasoning_effort=reasoning_effort,
            )
            files = await prepare_request_files(
                request.messages,
                request.extra_body,
                self.settings.request_timeout,
                self.settings.proxy,
            )
            yield format_sse(
                build_chunk(
                    response_id=response_id,
                    created=created,
                    model=resolved_model.canonical_id,
                    delta={"role": "assistant"},
                )
            )
            if request.tools:
                accumulated = ""
                async for chunk in self.generate_stream(
                    prompt=prompt,
                    upstream_model=resolved_model.upstream_name,
                    request=request,
                    files=files or None,
                ):
                    accumulated += self._extract_stream_text(chunk)

                tool_calls = self.parse_tool_calls(accumulated)
                if tool_calls:
                    streamed_tool_calls = [
                        {
                            "index": index,
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                            },
                        }
                        for index, tool_call in enumerate(tool_calls)
                    ]
                    yield format_sse(
                        build_chunk(
                            response_id=response_id,
                            created=created,
                            model=resolved_model.canonical_id,
                            delta={"tool_calls": streamed_tool_calls},
                        )
                    )
                    finish_reason = "tool_calls"
                else:
                    if accumulated:
                        yield format_sse(
                            build_chunk(
                                response_id=response_id,
                                created=created,
                                model=resolved_model.canonical_id,
                                delta={"content": accumulated},
                            )
                        )
                    finish_reason = "stop"
            else:
                async for chunk in self.generate_stream(
                    prompt=prompt,
                    upstream_model=resolved_model.upstream_name,
                    request=request,
                    files=files or None,
                ):
                    text_delta = self._extract_stream_text(chunk)
                    if not text_delta:
                        continue
                    yield format_sse(
                        build_chunk(
                            response_id=response_id,
                            created=created,
                            model=resolved_model.canonical_id,
                            delta={"content": text_delta},
                        )
                    )
                    finish_reason = "stop"

            yield format_sse(
                build_chunk(
                    response_id=response_id,
                    created=created,
                    model=resolved_model.canonical_id,
                    delta={},
                    finish_reason=finish_reason,
                )
            )
            yield format_sse("[DONE]")
        except Exception as exc:
            error = self._normalize_exception(exc)
            yield format_sse(
                {
                    "error": {
                        "message": error.message,
                        "type": "api_error",
                        "code": error.code,
                    }
                }
            )
            yield format_sse("[DONE]")
        finally:
            cleanup_prepared_files(files)

    def _extract_stream_text(self, chunk: Any) -> str:
        if isinstance(chunk, str):
            return chunk

        text_delta = getattr(chunk, "text_delta", None)
        if isinstance(text_delta, str):
            return text_delta

        text = getattr(chunk, "text", None)
        if isinstance(text, str):
            return text

        raise GatewayServiceError(
            message="Unsupported stream chunk type.",
            code="gateway_error",
            status_code=500,
        )

    async def generate_text(
        self,
        prompt: str,
        upstream_model: str,
        request: ChatCompletionRequest,
        files: list[Path] | None = None,
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
                files=files,
            )
            return response.text
        finally:
            await client.close()

    async def generate_stream(
        self,
        prompt: str,
        upstream_model: str,
        request: ChatCompletionRequest,
        files: list[Path] | None = None,
    ) -> AsyncGenerator[Any, None]:
        client = self.build_gemini_client()
        try:
            await client.init(
                timeout=self.settings.request_timeout,
                auto_refresh=False,
                auto_close=False,
            )
            async for chunk in client.generate_content_stream(
                prompt=prompt,
                model=upstream_model,
                files=files,
            ):
                yield chunk
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
        client = GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=self.settings.proxy,
        )
        if extra_cookies:
            client.cookies = extra_cookies
        return client

    def _normalize_exception(self, exc: Exception) -> GatewayServiceError:
        if isinstance(exc, GatewayServiceError):
            return exc
        if isinstance(exc, AuthError):
            return GatewayServiceError(
                message="Gemini upstream authentication failed. Check cookies.json.",
                code="upstream_auth_error",
                status_code=502,
            )
        if isinstance(exc, TimeoutError):
            return GatewayServiceError(
                message="Gemini upstream request timed out.",
                code="upstream_timeout",
                status_code=504,
            )
        if isinstance(exc, (APIError, GeminiError)):
            return GatewayServiceError(
                message=str(exc) or "Gemini upstream request failed.",
                code="upstream_error",
                status_code=502,
            )
        if isinstance(exc, (HTTPError, URLError, OSError)):
            return GatewayServiceError(
                message=str(exc) or "Failed to fetch or decode request attachment.",
                code="upstream_error",
                status_code=502,
            )
        return GatewayServiceError(
            message=str(exc) or "Unexpected gateway error.",
            code="upstream_error",
            status_code=500,
        )
