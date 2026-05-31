import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:8010/v1"
API_KEY = "gemini-api"
DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_REASONING_EFFORT = "standard"


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {
        "Authorization": f"Bearer {API_KEY}",
    }

    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = Request(url=url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=120) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code} error from {url}")
        print(body)
        raise
    except URLError as exc:
        print(f"Request failed for {url}: {exc}")
        raise


def request_stream(url: str, payload: dict) -> tuple[int, list[str]]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "text/event-stream",
    }
    request = Request(url=url, data=data, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=120) as response:  # noqa: S310
            events: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data: "):
                    continue
                events.append(line)
            return response.status, events
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code} error from {url}")
        print(body)
        raise
    except URLError as exc:
        print(f"Streaming request failed for {url}: {exc}")
        raise


def main() -> None:
    root_url = BASE_URL.removesuffix("/v1")

    print(f"Base URL: {BASE_URL}")
    print(f"API Key: {API_KEY}")
    print(f"Default model: {DEFAULT_MODEL}")
    print(f"Default reasoning effort: {DEFAULT_REASONING_EFFORT}")
    print("")

    health_status, health_body = request_json(f"{root_url}/health")
    print("[1] Health")
    print(f"status: {health_status}")
    print(json.dumps(health_body, ensure_ascii=False, indent=2))
    print("")

    models_status, models_body = request_json(f"{BASE_URL}/models")
    print("[2] Models")
    print(f"status: {models_status}")
    print(json.dumps(models_body, ensure_ascii=False, indent=2))
    print("")

    chat_payload = {
        "model": DEFAULT_MODEL,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "messages": [
            {
                "role": "user",
                "content": "请只回复 gateway ok",
            }
        ],
    }
    chat_status, chat_body = request_json(
        f"{BASE_URL}/chat/completions",
        method="POST",
        payload=chat_payload,
    )
    print("[3] Chat Completion")
    print(f"status: {chat_status}")
    print(json.dumps(chat_body, ensure_ascii=False, indent=2))
    print("")

    stream_payload = {
        "model": DEFAULT_MODEL,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "stream": True,
        "messages": [
            {
                "role": "user",
                "content": "请只回复 gateway stream ok",
            }
        ],
    }
    stream_status, stream_events = request_stream(
        f"{BASE_URL}/chat/completions",
        payload=stream_payload,
    )
    print("[4] Streaming Chat Completion")
    print(f"status: {stream_status}")
    for event in stream_events:
        print(event)

    if not any(event == "data: [DONE]" for event in stream_events):
        raise RuntimeError("Streaming test failed: missing data: [DONE]")
    print("")

    tools_payload = {
        "model": DEFAULT_MODEL,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "tool_choice": "required",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "获取指定城市天气",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "城市名称",
                            }
                        },
                        "required": ["city"],
                    },
                },
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": "你必须调用 get_weather 工具查询深圳天气，不要直接回答天气结果。",
            }
        ],
    }
    tools_status, tools_body = request_json(
        f"{BASE_URL}/chat/completions",
        method="POST",
        payload=tools_payload,
    )
    print("[5] Tools Chat Completion")
    print(f"status: {tools_status}")
    print(json.dumps(tools_body, ensure_ascii=False, indent=2))

    tool_calls = tools_body["choices"][0]["message"].get("tool_calls")
    if not tool_calls:
        raise RuntimeError("Tools test failed: missing tool_calls in response")


if __name__ == "__main__":
    main()
