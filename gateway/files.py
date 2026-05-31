from __future__ import annotations

import asyncio
import base64
import binascii
import mimetypes
from pathlib import Path
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote_to_bytes, urlsplit
from urllib.request import ProxyHandler, build_opener, urlopen

from gateway.schemas import ChatMessage


def _gateway_error(message: str, code: str) -> Exception:
    from gateway.service import GatewayServiceError

    return GatewayServiceError(
        message=message,
        code=code,
        status_code=400,
    )


def _sanitize_filename(name: str | None, default_stem: str) -> str:
    candidate = (name or "").strip().replace("\\", "/").split("/")[-1]
    if candidate:
        return candidate
    return f"{default_stem}.bin"


def _filename_from_url(url: str) -> str:
    path = urlsplit(url).path
    name = Path(path).name
    return _sanitize_filename(name, "remote_input")


def _ensure_extension(name: str, content_type: str | None) -> str:
    if Path(name).suffix:
        return name

    extension = mimetypes.guess_extension(content_type or "") or ".bin"
    return f"{name}{extension}"


def _write_temp_file(data: bytes, name: str) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="gemini_gateway_"))
    path = temp_dir / name
    path.write_bytes(data)
    return path


def _decode_data_url(url: str) -> tuple[bytes, str | None]:
    header, separator, payload = url.partition(",")
    if not separator or not header.startswith("data:"):
        raise _gateway_error("Invalid data URL image input.", "image_fetch_failed")

    metadata = header[5:]
    metadata_parts = metadata.split(";") if metadata else []
    content_type = metadata_parts[0] if metadata_parts and "/" in metadata_parts[0] else None
    is_base64 = "base64" in metadata_parts[1:] or metadata.endswith(";base64")

    try:
        if is_base64:
            return base64.b64decode(payload, validate=True), content_type
        return unquote_to_bytes(payload), content_type
    except (binascii.Error, ValueError) as exc:
        raise _gateway_error(
            "Invalid base64 payload in data URL image input.",
            "image_fetch_failed",
        ) from exc


def _download_remote_file(
    url: str,
    timeout: int,
    proxy: str | None = None,
) -> tuple[bytes, str | None]:
    if proxy:
        opener = build_opener(
            ProxyHandler(
                {
                    "http": proxy,
                    "https": proxy,
                }
            )
        )
        response_context = opener.open(url, timeout=timeout)  # noqa: S310
    else:
        response_context = urlopen(url, timeout=timeout)  # noqa: S310

    with response_context as response:
        content = response.read()
        content_type = response.headers.get_content_type()
        return content, content_type


async def _prepare_image_part(url: str, timeout: int, proxy: str | None = None) -> Path:
    if url.startswith("data:"):
        data, content_type = _decode_data_url(url)
        filename = _ensure_extension("message_image", content_type)
        return _write_temp_file(data, filename)

    if url.startswith("http://") or url.startswith("https://"):
        try:
            data, content_type = await asyncio.to_thread(
                _download_remote_file,
                url,
                timeout,
                proxy,
            )
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise _gateway_error(
                f"Failed to fetch remote image: {url}",
                "image_fetch_failed",
            ) from exc
        filename = _ensure_extension(_filename_from_url(url), content_type)
        return _write_temp_file(data, filename)

    raise _gateway_error(
        "Only data:, http://, and https:// image URLs are supported.",
        "image_fetch_failed",
    )


async def _prepare_extra_file(file_spec: dict[str, Any]) -> Path:
    if not isinstance(file_spec, dict):
        raise _gateway_error(
            "extra_body.files items must be objects.",
            "file_decode_failed",
        )

    encoded = file_spec.get("data_base64")
    if not isinstance(encoded, str) or not encoded:
        raise _gateway_error(
            "extra_body.files[].data_base64 is required.",
            "file_decode_failed",
        )

    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _gateway_error(
            "extra_body.files contains invalid base64 data.",
            "file_decode_failed",
        ) from exc

    content_type = file_spec.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise _gateway_error(
            "extra_body.files[].content_type must be a string.",
            "file_decode_failed",
        )

    filename = _sanitize_filename(file_spec.get("name"), "extra_file")
    filename = _ensure_extension(filename, content_type)
    return _write_temp_file(data, filename)


async def prepare_request_files(
    messages: list[ChatMessage],
    extra_body: dict[str, Any] | None,
    timeout: int,
    proxy: str | None = None,
) -> list[Path]:
    files: list[Path] = []
    try:
        for message in messages:
            if not isinstance(message.content, list):
                continue

            for part in message.content:
                if part.type != "image_url" or not isinstance(part.image_url, dict):
                    continue
                url = part.image_url.get("url")
                if not isinstance(url, str) or not url:
                    raise _gateway_error(
                        "image_url part requires a non-empty url.",
                        "image_fetch_failed",
                    )
                files.append(await _prepare_image_part(url, timeout, proxy))

        extra_files = (extra_body or {}).get("files", [])
        if not isinstance(extra_files, list):
            raise _gateway_error(
                "extra_body.files must be an array.",
                "file_decode_failed",
            )

        for file_spec in extra_files:
            files.append(await _prepare_extra_file(file_spec))

        return files
    except Exception:
        cleanup_prepared_files(files)
        raise


def cleanup_prepared_files(files: list[Path]) -> None:
    for path in files:
        try:
            if path.exists():
                path.unlink()
            parent = path.parent
            if parent.exists():
                parent.rmdir()
        except OSError:
            continue
