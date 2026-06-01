from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, AsyncGenerator, TYPE_CHECKING
from urllib.error import HTTPError, URLError
from uuid import uuid4

from gateway.account import (
    GatewayAccountSnapshot,
    evaluate_account_mode,
    validate_required_account_level,
)
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
    GatewayModelSpec("gemini-3-flash", "gemini-3-flash"),
    GatewayModelSpec("gemini-3-flash-thinking", "gemini-3-flash-thinking"),
    GatewayModelSpec("gemini-3-pro", "gemini-3-pro"),
)

class GatewayService:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._models_by_id = {
            model.canonical_id: model
            for model in CANONICAL_MODELS
        }
        self._cached_cookies: dict[str, str] | None = None
        self._account_snapshot: GatewayAccountSnapshot | None = None
        self._shared_client: GeminiClient | None = None
        self._shared_client_generation = 0
        self._active_client_refs: dict[int, int] = {}
        self._retired_clients: dict[int, GeminiClient] = {}
        self._is_warmed_up = False
        self._shared_client_lock = asyncio.Lock()
        self._cookie_persist_task: asyncio.Task[None] | None = None

    def list_models(self) -> ModelListResponse:
        return ModelListResponse(
            data=[ModelCard(id=model.canonical_id) for model in CANONICAL_MODELS]
        )

    def resolve_model(self, public_name: str | None) -> GatewayModelSpec:
        requested_name = public_name or self.settings.default_model
        model = self._models_by_id.get(requested_name)
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
        cookies = self._merge_with_newer_upstream_cookie_cache(
            cookies=cookies,
            cookies_path=cookies_path,
        )
        if "__Secure-1PSID" not in cookies:
            raise GatewayServiceError(
                message="Missing __Secure-1PSID in cookies JSON.",
                code="missing_cookies",
                status_code=500,
            )
        return cookies

    def get_cached_cookies(self) -> dict[str, str]:
        if self._cached_cookies is None:
            self._cached_cookies = self.load_cookies()
        return self._cached_cookies

    def get_account_snapshot(self) -> GatewayAccountSnapshot | None:
        return self._account_snapshot

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

    def _serialize_cookies_for_json(self, cookies: Any) -> dict[str, str]:
        if cookies is None:
            return {}

        if isinstance(cookies, dict):
            return {
                name: value
                for name, value in cookies.items()
                if isinstance(name, str) and isinstance(value, str)
            }

        to_dict = getattr(cookies, "to_dict", None)
        if callable(to_dict):
            serialized = to_dict()
            if isinstance(serialized, dict):
                return {
                    name: value
                    for name, value in serialized.items()
                    if isinstance(name, str) and isinstance(value, str)
                }

        items = getattr(cookies, "items", None)
        if callable(items):
            return {
                name: value
                for name, value in items()
                if isinstance(name, str) and isinstance(value, str)
            }

        jar = getattr(cookies, "jar", None)
        if jar is not None:
            serialized: dict[str, str] = {}
            for cookie in jar:
                name = getattr(cookie, "name", None)
                value = getattr(cookie, "value", None)
                if isinstance(name, str) and isinstance(value, str):
                    serialized[name] = value
            return serialized

        if isinstance(cookies, list):
            return self._cookies_from_list(cookies)

        return {}

    def persist_cookies(self, cookies: Any, *, force: bool = False) -> None:
        if not force and not self.settings.cookie_persist_enabled:
            return

        serialized = self._merge_serialized_cookies_with_cache(
            self._serialize_cookies_for_json(cookies)
        )

        if "__Secure-1PSID" not in serialized:
            raise GatewayServiceError(
                message="Cannot persist cookies without __Secure-1PSID.",
                code="cookie_persist_failed",
                status_code=500,
            )

        payload = {
            "cookies": dict(sorted(serialized.items())),
            "updated_at": int(time.time()),
        }
        cookies_path = Path(self.settings.cookies_json_path)
        temp_path = cookies_path.with_name(
            f".{cookies_path.name}.{uuid4().hex}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(cookies_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        self._cached_cookies = dict(payload["cookies"])

    async def start_cookie_persist_task(self) -> None:
        if not self.settings.cookie_persist_enabled:
            return
        if self._cookie_persist_task is not None and not self._cookie_persist_task.done():
            return
        self._cookie_persist_task = asyncio.create_task(self._run_cookie_persist_loop())

    async def stop_cookie_persist_task(self) -> None:
        task = self._cookie_persist_task
        self._cookie_persist_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_cookie_persist_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.cookie_persist_interval_seconds)
                try:
                    await self.flush_runtime_cookies()
                except Exception as exc:
                    print(f"Warning: failed to persist runtime cookies: {exc}")
        except asyncio.CancelledError:
            raise

    async def flush_runtime_cookies(self) -> bool:
        client = self._shared_client
        if client is None:
            return False

        runtime_cookies = self._serialize_cookies_for_json(
            getattr(client, "cookies", None)
        )
        if not runtime_cookies:
            return False

        merged = self._merge_serialized_cookies_with_cache(runtime_cookies)
        if merged == (self._cached_cookies or {}):
            return False

        self.persist_cookies(merged)
        return True

    def _merge_serialized_cookies_with_cache(
        self,
        serialized: dict[str, str],
    ) -> dict[str, str]:
        merged = dict(serialized)
        if "__Secure-1PSID" not in merged and self._cached_cookies:
            secure_1psid = self._cached_cookies.get("__Secure-1PSID")
            if isinstance(secure_1psid, str) and secure_1psid:
                merged["__Secure-1PSID"] = secure_1psid
        return merged

    def _sync_cached_cookies_from_client(self, client: GeminiClient) -> None:
        runtime_cookies = self._serialize_cookies_for_json(
            getattr(client, "cookies", None)
        )
        if not runtime_cookies:
            return

        merged = dict(self._cached_cookies or {})
        merged.update(runtime_cookies)
        self._cached_cookies = merged

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
        client, generation = await self._acquire_shared_client()
        try:
            for attempt in range(2):
                try:
                    response = await client.generate_content(
                        prompt=prompt,
                        model=upstream_model,
                        files=files,
                    )
                    return response.text
                except Exception as exc:
                    if attempt == 0 and self._should_rebuild_shared_client(exc):
                        refreshed_from_browser = False
                        if self._should_refresh_browser_cookies_after_error(exc):
                            refreshed_from_browser = self.refresh_cookies_from_browser()
                            if not refreshed_from_browser:
                                raise
                        rebuilt_client, rebuilt_generation = (
                            await self._rebuild_shared_client_after_failure(
                                failed_client=client,
                                failed_generation=generation,
                                sync_failed_client_cookies=not refreshed_from_browser,
                            )
                        )
                        previous_generation = generation
                        client = rebuilt_client
                        generation = rebuilt_generation
                        await self._release_shared_client(previous_generation)
                        continue
                    raise
        finally:
            await self._release_shared_client(generation)

    async def generate_stream(
        self,
        prompt: str,
        upstream_model: str,
        request: ChatCompletionRequest,
        files: list[Path] | None = None,
    ) -> AsyncGenerator[Any, None]:
        client, generation = await self._acquire_shared_client()
        try:
            for attempt in range(2):
                yielded_any_chunk = False
                try:
                    async for chunk in client.generate_content_stream(
                        prompt=prompt,
                        model=upstream_model,
                        files=files,
                    ):
                        yielded_any_chunk = True
                        yield chunk
                    return
                except Exception as exc:
                    if (
                        attempt == 0
                        and not yielded_any_chunk
                        and self._should_rebuild_shared_client(exc)
                    ):
                        refreshed_from_browser = False
                        if self._should_refresh_browser_cookies_after_error(exc):
                            refreshed_from_browser = self.refresh_cookies_from_browser()
                            if not refreshed_from_browser:
                                raise
                        rebuilt_client, rebuilt_generation = (
                            await self._rebuild_shared_client_after_failure(
                                failed_client=client,
                                failed_generation=generation,
                                sync_failed_client_cookies=not refreshed_from_browser,
                            )
                        )
                        previous_generation = generation
                        client = rebuilt_client
                        generation = rebuilt_generation
                        await self._release_shared_client(previous_generation)
                        continue
                    raise
        finally:
            await self._release_shared_client(generation)

    def _build_client_from_cached_cookies(self) -> GeminiClient:
        from gemini_webapi import GeminiClient

        cookies = self.get_cached_cookies()
        psid = cookies.get("__Secure-1PSID")
        psidts = cookies.get("__Secure-1PSIDTS", "")
        client = GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=self.settings.proxy,
        )
        return client

    async def warmup(self) -> None:
        if self._is_warmed_up and self._shared_client is not None:
            return

        async with self._shared_client_lock:
            if self._is_warmed_up and self._shared_client is not None:
                return

            client = self._shared_client or self._build_client_from_cached_cookies()
            try:
                await self._init_shared_client(client)
            except Exception:
                self._shared_client = None
                self._account_snapshot = None
                self._is_warmed_up = False
                await client.close()
                raise

    async def shutdown(self) -> None:
        async with self._shared_client_lock:
            client = self._shared_client
            generation = self._shared_client_generation
            self._shared_client = None
            self._account_snapshot = None
            self._is_warmed_up = False
            clients_to_close = self._collect_releasable_retired_clients_locked()
            current_client_to_close = self._retire_client_locked(generation, client)
            if current_client_to_close is not None:
                clients_to_close.append(current_client_to_close)

        shutdown_error: Exception | None = None
        if client is not None:
            try:
                self.persist_cookies(getattr(client, "cookies", None), force=True)
            except Exception as exc:
                shutdown_error = exc

        for current_client in clients_to_close:
            try:
                await current_client.close()
            except Exception as exc:
                if shutdown_error is None:
                    shutdown_error = exc

        if shutdown_error is not None:
            raise shutdown_error

    async def get_shared_client(self) -> GeminiClient:
        await self.warmup()
        if self._shared_client is None:
            raise GatewayServiceError(
                message="Gemini client warmup failed.",
                code="upstream_error",
                status_code=500,
            )
        return self._shared_client

    def build_gemini_client(self) -> GeminiClient:
        return self._build_client_from_cached_cookies()

    async def _init_shared_client(self, client: GeminiClient) -> GeminiClient:
        await client.init(
            timeout=self.settings.request_timeout,
            auto_refresh=True,
            auto_close=False,
        )
        snapshot = await self._build_account_snapshot(client)
        if self.settings.account_strict_mode:
            validate_required_account_level(
                snapshot,
                self.settings.account_required_level,
            )

        self._shared_client_generation += 1
        self._shared_client = client
        self._account_snapshot = snapshot
        self._is_warmed_up = True
        return client

    async def _build_account_snapshot(
        self,
        client: GeminiClient,
    ) -> GatewayAccountSnapshot:
        raw_status = getattr(client, "account_status", None)
        raw_name = getattr(raw_status, "name", "UNKNOWN")
        raw_code = getattr(raw_status, "value", None)

        probe: dict[str, Any] = {}
        if self.settings.account_probe_enabled:
            probe_result = await client.inspect_account_status()
            if isinstance(probe_result, dict):
                probe = probe_result

        summary = probe.get("summary", {}) if isinstance(probe, dict) else {}
        rejected_probes = summary.get("rejected_probes", [])
        if not isinstance(rejected_probes, list):
            rejected_probes = []

        chat_available = raw_name not in {
            "LOCATION_REJECTED",
            "ACCOUNT_REJECTED",
            "ACCESS_TEMPORARILY_UNAVAILABLE",
            "ACCOUNT_REJECTED_BY_GUARDIAN",
            "GUARDIAN_APPROVAL_REQUIRED",
        }
        advanced_models_available = self._detect_advanced_models_available(
            client,
            raw_name,
        )
        deep_research_available = bool(
            summary.get("deep_research_feature_present", False)
        )
        full_web_capability_available = (
            chat_available
            and advanced_models_available
            and deep_research_available
            and not rejected_probes
        )

        unavailable_reasons: list[str] = []
        if not chat_available:
            unavailable_reasons.append("chat_unavailable")
        if not advanced_models_available:
            unavailable_reasons.append("advanced_models_unavailable")
        if not deep_research_available:
            unavailable_reasons.append("deep_research_unavailable")
        unavailable_reasons.extend(
            f"probe_rejected:{probe_name}"
            for probe_name in rejected_probes
            if isinstance(probe_name, str)
        )

        snapshot = GatewayAccountSnapshot(
            raw_account_status=raw_name,
            raw_account_status_code=raw_code if isinstance(raw_code, int) else None,
            chat_available=chat_available,
            advanced_models_available=advanced_models_available,
            deep_research_available=deep_research_available,
            full_web_capability_available=full_web_capability_available,
            mode="unknown",
            unavailable_reasons=unavailable_reasons,
        )
        return evaluate_account_mode(snapshot)

    def _detect_advanced_models_available(
        self,
        client: GeminiClient,
        raw_status_name: str,
    ) -> bool:
        registry = getattr(client, "_model_registry", None)
        if isinstance(registry, dict) and registry:
            advanced_candidates = [
                model
                for model in registry.values()
                if getattr(model, "advanced_only", False)
            ]
            if advanced_candidates:
                return any(
                    bool(getattr(model, "is_available", False))
                    for model in advanced_candidates
                )

        return raw_status_name == "AVAILABLE"

    async def _acquire_shared_client(self) -> tuple[GeminiClient, int]:
        await self.warmup()
        async with self._shared_client_lock:
            client = self._shared_client
            if client is None:
                raise GatewayServiceError(
                    message="Gemini client warmup failed.",
                    code="upstream_error",
                    status_code=500,
                )

            generation = self._shared_client_generation
            self._active_client_refs[generation] = (
                self._active_client_refs.get(generation, 0) + 1
            )
            return client, generation

    async def _release_shared_client(self, generation: int) -> None:
        client_to_close: GeminiClient | None = None
        async with self._shared_client_lock:
            ref_count = self._active_client_refs.get(generation)
            if ref_count is None:
                return

            if ref_count <= 1:
                self._active_client_refs.pop(generation, None)
                client_to_close = self._retired_clients.pop(generation, None)
            else:
                self._active_client_refs[generation] = ref_count - 1

        if client_to_close is not None:
            await client_to_close.close()

    async def _rebuild_shared_client_after_failure(
        self,
        failed_client: GeminiClient,
        failed_generation: int,
        *,
        sync_failed_client_cookies: bool = True,
    ) -> tuple[GeminiClient, int]:
        client_to_close: GeminiClient | None = None
        rebuilt_client_to_close: GeminiClient | None = None
        rebuild_error: Exception | None = None
        async with self._shared_client_lock:
            if (
                self._shared_client is not None
                and self._shared_client is not failed_client
            ):
                generation = self._shared_client_generation
                self._active_client_refs[generation] = (
                    self._active_client_refs.get(generation, 0) + 1
                )
                return self._shared_client, generation

            if sync_failed_client_cookies:
                self._sync_cached_cookies_from_client(failed_client)
            client = self._build_client_from_cached_cookies()
            try:
                await self._init_shared_client(client)
            except Exception as exc:
                rebuild_error = exc
                rebuilt_client_to_close = client
                if self._shared_client is failed_client:
                    self._shared_client = None
                    self._account_snapshot = None
                    self._is_warmed_up = False
                client_to_close = self._retire_client_locked(
                    failed_generation,
                    failed_client,
                )
            else:
                generation = self._shared_client_generation
                self._active_client_refs[generation] = (
                    self._active_client_refs.get(generation, 0) + 1
                )
                client_to_close = self._retire_client_locked(
                    failed_generation,
                    failed_client,
                )

        if client_to_close is not None:
            await client_to_close.close()

        if rebuilt_client_to_close is not None:
            await rebuilt_client_to_close.close()

        if rebuild_error is not None:
            raise rebuild_error

        return client, generation

    def _should_rebuild_shared_client(self, exc: Exception) -> bool:
        return isinstance(exc, (AuthError, TimeoutError, APIError, GeminiError))

    def _should_refresh_browser_cookies_after_error(self, exc: Exception) -> bool:
        return (
            self.settings.browser_cookie_refresh_enabled
            and self.settings.browser_cookie_refresh_on_auth_error
            and isinstance(exc, AuthError)
        )

    def refresh_cookies_from_browser(self) -> bool:
        if not self.settings.browser_cookie_refresh_enabled:
            return False

        try:
            from gateway.refresh_cookies import (
                BrowserCookieRefreshError,
                refresh_browser_cookies_to_file,
            )

            selection = refresh_browser_cookies_to_file(
                self.settings.cookies_json_path,
                profile_dir=self.settings.browser_profile_dir,
                headless=self.settings.browser_headless,
                login_wait_seconds=self.settings.browser_login_wait_seconds,
                poll_interval_seconds=self.settings.browser_poll_interval_seconds,
                page_load_timeout_seconds=self.settings.browser_page_load_timeout_seconds,
                print_summary=False,
            )
        except BrowserCookieRefreshError as exc:
            if exc.manual_login_required:
                print(
                    "Warning: browser cookies require manual profile login: "
                    f"{exc}"
                )
            else:
                print(f"Warning: failed to refresh browser cookies: {exc}")
            return False
        except Exception as exc:
            print(f"Warning: failed to refresh browser cookies: {exc}")
            return False

        self._cached_cookies = dict(selection.cookies)
        return True

    def _merge_with_newer_upstream_cookie_cache(
        self,
        *,
        cookies: dict[str, str],
        cookies_path: Path,
    ) -> dict[str, str]:
        secure_1psid = cookies.get("__Secure-1PSID")
        if not isinstance(secure_1psid, str) or not secure_1psid:
            return cookies

        cache_root = os.getenv("GEMINI_COOKIE_PATH")
        cache_dir = (
            Path(cache_root)
            if cache_root
            else Path(tempfile.gettempdir()) / "gemini_webapi"
        )
        upstream_cache_path = cache_dir / f".cached_cookies_{secure_1psid}.json"
        if not upstream_cache_path.is_file():
            return cookies

        try:
            upstream_stat = upstream_cache_path.stat()
            cookies_stat = cookies_path.stat()
        except OSError:
            return cookies

        if upstream_stat.st_mtime <= cookies_stat.st_mtime:
            return cookies

        try:
            upstream_raw = json.loads(
                upstream_cache_path.read_text(encoding="utf-8")
            )
            upstream_cookies = self._extract_cookies(upstream_raw)
        except Exception:
            return cookies

        if "__Secure-1PSID" not in upstream_cookies:
            return cookies

        return upstream_cookies

    def _retire_client_locked(
        self,
        generation: int,
        client: GeminiClient | None,
    ) -> GeminiClient | None:
        if client is None:
            return None

        if self._active_client_refs.get(generation, 0) > 0:
            self._retired_clients[generation] = client
            return None

        self._retired_clients.pop(generation, None)
        return client

    def _collect_releasable_retired_clients_locked(self) -> list[GeminiClient]:
        releasable_generations = [
            generation
            for generation in self._retired_clients
            if self._active_client_refs.get(generation, 0) == 0
        ]
        clients_to_close = [
            self._retired_clients.pop(generation)
            for generation in releasable_generations
        ]
        return clients_to_close

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
