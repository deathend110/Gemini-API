from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    text: str | None = None
    image_url: dict[str, Any] | None = None


class ChatToolFunction(BaseModel):
    name: str
    description: str | None = None
    arguments: str | None = None
    parameters: dict[str, Any] | None = None


class ChatToolCall(BaseModel):
    id: str
    type: str = "function"
    function: ChatToolFunction


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list[ChatContentPart] | None
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ChatToolCall] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] | None = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "gemini-webapi-gateway"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelCard]


class ChatCompletionResponseMessage(BaseModel):
    role: str
    content: str | None
    tool_calls: list[ChatToolCall] | None = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatCompletionResponseMessage
    finish_reason: str | None


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage | None = None
