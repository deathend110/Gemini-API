# Gemini OpenAI-Compatible Gateway Design

## Goal

Build a local FastAPI gateway inside this repository that exposes an OpenAI-compatible API on top of `gemini_webapi`, so AstrBot and other OpenAI-style clients can connect by only configuring:

- `base_url`
- `api_key`
- `model`
- `reasoning_effort`

The gateway must be general-purpose enough for immediate AstrBot use and later reuse by the local project at `G:\VSCODE-G\Fitness Agent MVP`.

## Scope

### In Scope

- Dedicated local gateway service under a new `gateway/` directory
- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /chat/completions` compatibility alias
- Standard and streaming chat completions
- OpenAI-style `tools` / `tool_calls`
- Image input via OpenAI multimodal `messages[].content[]`
- File input via gateway extension fields
- Configurable model and reasoning effort
- Local Bearer auth with a configured or generated API key

### Out of Scope For V1

- `/v1/responses`
- Embeddings
- Audio input/output
- Server-side conversation memory
- Guaranteed strict JSON mode via `response_format`
- Native Gemini tool API integration beyond prompt-constrained tool calling

## Users and Clients

### Primary Clients

- AstrBot through OpenAI-compatible provider settings
- Fitness Agent MVP through OpenAI-style `chat/completions`

### Client Assumptions

- Clients send full `messages[]` history per request
- Clients authenticate with `Authorization: Bearer <api_key>`
- Clients call `.../v1` endpoints by default
- Clients may request streaming
- Clients may provide OpenAI-style tool definitions

## High-Level Architecture

Create a standalone gateway under `gateway/` without modifying the core wrapper package structure.

Suggested structure:

- `gateway/main.py`: FastAPI entrypoint and route registration
- `gateway/config.py`: runtime settings and environment resolution
- `gateway/auth.py`: Bearer key validation
- `gateway/schemas.py`: OpenAI-compatible request/response models
- `gateway/service.py`: request translation and Gemini invocation
- `gateway/streaming.py`: SSE formatting
- `gateway/files.py`: image/file loading helpers
- `gateway/README.md`: startup and client integration instructions

The gateway uses the local `gemini_webapi` implementation from this repository and reads:

- `cookies.json`
- proxy settings
- default model
- default reasoning effort

## Public Interface

### Base URL

The gateway will advertise:

- `http://127.0.0.1:8000/v1`

For compatibility, it will also accept:

- `POST /chat/completions`

### Authentication

All protected endpoints require:

- `Authorization: Bearer <api_key>`

Behavior:

- If a configured API key exists, use it
- Otherwise generate one at startup and print it to console

### Endpoints

#### `GET /health`

Purpose:

- Simple readiness probe

Response:

- basic JSON showing service status

#### `GET /v1/models`

Purpose:

- Return available gateway model identifiers in OpenAI-compatible list format

Initial exposed model names:

- `gemini-3.1-pro`
- `gemini-3.5-flash`
- `gemini-3.1-flash-lite`

The gateway may additionally accept aliases internally, but the canonical names above are the public contract.

#### `POST /v1/chat/completions`

Purpose:

- Main OpenAI-compatible chat endpoint

Supported request fields:

- `model`
- `messages`
- `stream`
- `tools`
- `tool_choice`
- `temperature`
- `max_tokens`
- `reasoning_effort`
- `extra_body.reasoning_effort`
- `extra_body.files`

Ignored-but-accepted fields:

- `top_p`
- `frequency_penalty`
- `presence_penalty`
- `n`
- `user`
- `stop`

#### `POST /chat/completions`

Purpose:

- Alias for clients that omit `/v1`

Behavior:

- Reuse the exact same processing logic as `/v1/chat/completions`

## Model and Reasoning Effort

Model selection and reasoning effort are independent.

### Model

Clients explicitly choose a model via `model`.

Canonical public model names:

- `gemini-3.1-pro`
- `gemini-3.5-flash`
- `gemini-3.1-flash-lite`

Optional aliases may be accepted internally:

- `3.1-pro`
- `3.5-flash`
- `3.1-flash-lite`

### Reasoning Effort

Reasoning effort is a separate optional field.

Supported values:

- `standard`
- `extended`

Resolution order:

1. top-level `reasoning_effort`
2. `extra_body.reasoning_effort`
3. configured default

Default:

- `standard`

The gateway must not silently map reasoning effort to a different model. It may only affect gateway-side handling or model-specific invocation policy when explicitly documented.

## Message Mapping

The gateway is stateless and relies on the client to send the full message history.

### System Messages

- Collect all `system` messages in order
- Merge them into one system instruction block
- Append gateway-managed tool-calling protocol instructions if tools are present

### User Messages

Supported user content shapes:

- plain string content
- OpenAI multimodal array content

For multimodal arrays:

- `type: "text"` becomes user prompt text
- `type: "image_url"` becomes Gemini file/image input

### Assistant Messages

- Preserve prior assistant text as context
- If prior assistant message contains tool calls, serialize it into a structured history segment

### Tool Messages

- Convert `role: "tool"` content into a structured prompt segment
- Preserve `tool_call_id`
- Associate tool result with the requested tool name when available

## Image and File Input

### Images

V1 must support OpenAI-style image input inside `messages[].content[]`.

Supported `image_url.url` forms:

- `http://...`
- `https://...`
- `data:image/...;base64,...`

Processing:

- remote URLs are downloaded into memory or temp files
- `data:` URLs are decoded into bytes
- decoded assets are passed into Gemini as files

### Files

V1 must support gateway-specific file input via an extension field.

Supported format:

```json
{
  "extra_body": {
    "files": [
      {
        "name": "report.txt",
        "content_type": "text/plain",
        "data_base64": "..."
      }
    ]
  }
}
```

Expected V1 behavior:

- text-like files are decoded and attached as Gemini file inputs
- invalid or undecodable file payloads return a structured error

## Tool Calling

### Input Contract

Accept standard OpenAI `tools` entries with:

- `type: "function"`
- `function.name`
- `function.description`
- `function.parameters`

### Gemini-Side Strategy

Because this repository does not expose native OpenAI tool calling, the gateway will inject tool definitions and output constraints into the system prompt.

Gemini will be instructed:

- answer normally when no tool is needed
- output only strict JSON when tool use is required
- never wrap tool JSON in explanatory text

Required JSON shape from Gemini:

```json
{
  "tool_calls": [
    {
      "name": "tool_name",
      "arguments": {
        "arg1": "value1"
      }
    }
  ]
}
```

### Gateway Parsing Rule

Treat a reply as a tool call only when:

- top-level value is an object
- `tool_calls` exists
- `tool_calls` is a non-empty array
- each item has `name`
- each item has `arguments` as an object or JSON-decodable string

Otherwise treat the reply as normal assistant text.

### OpenAI Response Mapping

Non-streaming tool response:

- populate `choices[0].message.tool_calls`
- set `choices[0].message.content` to empty string
- set `finish_reason` to `"tool_calls"`

Normal text response:

- populate `choices[0].message.content`
- set `finish_reason` to `"stop"`

### Streaming Tool Calls

When `stream=true`, tool calls should be emitted as OpenAI-style chunk deltas under:

- `delta.tool_calls`

### Tool Result Follow-Up

When the client later sends a `role: "tool"` message, the gateway includes that tool result in the next Gemini prompt so Gemini can continue the response naturally.

## Response Mapping

### Non-Streaming

Return OpenAI-compatible `chat.completion` payloads.

Expected behavior:

- use canonical `id`, `object`, `created`, `model`
- include one `choice`
- map Gemini text to `choices[0].message.content`
- map tool decisions to `choices[0].message.tool_calls`

### Streaming

Return `text/event-stream`.

For text:

- emit `chat.completion.chunk`
- put text increments in `delta.content`

For tool calls:

- emit `delta.tool_calls`

Completion terminator:

- `data: [DONE]`

## Error Handling

Use OpenAI-compatible error format:

```json
{
  "error": {
    "message": "Detailed error message",
    "type": "api_error",
    "code": "upstream_error"
  }
}
```

V1 gateway error codes:

- `unauthorized`
- `missing_cookies`
- `invalid_model`
- `invalid_reasoning_effort`
- `image_fetch_failed`
- `file_decode_failed`
- `upstream_auth_error`
- `upstream_timeout`
- `upstream_error`

## Runtime Configuration

Required configuration surface:

- `host` default `127.0.0.1`
- `port` default `8000`
- `api_key` configured or generated at startup
- `cookies_json_path` default `./cookies.json`
- `proxy` from env or fallback `http://127.0.0.1:10090/`
- `default_model` default `gemini-3.5-flash`
- `default_reasoning_effort` default `standard`
- `request_timeout` default `300`
- `allowed_models` default to the three canonical Gemini model names

## Startup UX

On startup, print:

- `Base URL: http://127.0.0.1:8000/v1`
- `API Key: <value>`
- `Default model: gemini-3.5-flash`
- `Default reasoning effort: standard`

## Compatibility Notes

### AstrBot

Expected connection settings:

- `API Base URL`: `http://127.0.0.1:8000/v1`
- `API Key`: generated or configured local key

Important compatibility goals:

- OpenAI `messages`
- streaming
- `tools`
- image input through `image_url`

### Fitness Agent MVP

Expected future migration path:

- replace DeepSeek-style `base_url`
- replace API key
- keep OpenAI-style request format

Important compatibility goals:

- stateless full-history `messages[]`
- standard `/chat/completions`
- future reuse of the same local gateway contract

## Verification Criteria

V1 is acceptable when all of the following are true:

- service starts and prints base URL and API key
- `GET /health` succeeds
- `GET /v1/models` returns expected model identifiers
- `POST /v1/chat/completions` works for plain text
- `POST /chat/completions` works as alias
- `stream=true` returns valid SSE chunks and `[DONE]`
- tool definitions can produce valid `tool_calls`
- image input through `image_url` reaches Gemini successfully
- file input via `extra_body.files` is accepted for supported file types
- AstrBot can connect using OpenAI-compatible provider settings

## Risks and Tradeoffs

- Tool calling is prompt-constrained, not native Gemini structured tool calling
- V1 remains stateless, so clients must send full context
- File input uses gateway extension fields, not a universal OpenAI standard
- Different Gemini models may expose different multimodal/tool reliability characteristics

## Recommended Next Step

After spec approval:

- create an implementation plan
- then implement the gateway under `gateway/`
