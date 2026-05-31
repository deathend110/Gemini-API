import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from curl_cffi.requests import AsyncSession

from gemini_webapi import GeminiClient


def load_cookies(path: str = "cookies.json") -> tuple[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    # Support both the simple flat format and the CLI-persisted {"cookies": {...}} format.
    if isinstance(data, dict) and isinstance(data.get("cookies"), dict):
        data = data["cookies"]

    secure_1psid = data.get("__Secure-1PSID", "")
    secure_1psidts = data.get("__Secure-1PSIDTS", "")

    if not secure_1psid:
        raise ValueError("cookies.json missing __Secure-1PSID")

    return secure_1psid, secure_1psidts


def resolve_proxy() -> str:
    proxy = (
        os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
        or os.getenv("ALL_PROXY")
        or os.getenv("all_proxy")
    )
    return proxy or "http://127.0.0.1:10090/"


async def preflight_proxy(proxy: str) -> None:
    async with AsyncSession(
        impersonate="chrome",
        proxy=proxy,
        allow_redirects=True,
        verify=True,
    ) as session:
        response = await session.get("https://www.google.com", timeout=20)
        response.raise_for_status()


async def main():
    secure_1psid, secure_1psidts = load_cookies()
    proxy = resolve_proxy()

    print(f"python: {sys.executable}")
    print(f"proxy: {proxy}")

    await preflight_proxy(proxy)

    client = GeminiClient(
        secure_1psid=secure_1psid,
        secure_1psidts=secure_1psidts,
        proxy=proxy,
    )

    try:
        await client.init(timeout=30, auto_refresh=False, verbose=True)
        response = await client.generate_content("你好，请只回复 test ok")
        print(response.text)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
