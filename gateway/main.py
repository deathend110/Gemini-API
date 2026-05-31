from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.auth import build_bearer_verifier
from gateway.config import GatewaySettings
from gateway.schemas import ChatCompletionRequest, ModelListResponse
from gateway.service import GatewayService, GatewayServiceError


def get_gateway_service(request: Request) -> Any:
    return request.app.state.gateway_service


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    resolved_settings = settings or GatewaySettings()
    verify_bearer = build_bearer_verifier(resolved_settings)
    gateway_service = GatewayService(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await app.state.gateway_service.warmup()
        try:
            yield
        finally:
            await app.state.gateway_service.shutdown()

    app = FastAPI(
        title="Gemini OpenAI Gateway",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.gateway_service = gateway_service

    @app.exception_handler(GatewayServiceError)
    async def handle_gateway_service_error(
        _: Request, exc: GatewayServiceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": exc.message,
                    "type": "api_error",
                    "code": exc.code,
                }
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models", dependencies=[Depends(verify_bearer)], response_model=ModelListResponse)
    def list_models(service: Any = Depends(get_gateway_service)) -> Any:
        return service.list_models()

    @app.get("/v1/account/status", dependencies=[Depends(verify_bearer)])
    def get_account_status(service: Any = Depends(get_gateway_service)) -> dict[str, object]:
        snapshot = service.get_account_snapshot()
        if snapshot is None:
            raise GatewayServiceError(
                message="Gateway account snapshot unavailable.",
                code="account_snapshot_unavailable",
                status_code=503,
            )
        return snapshot.to_dict()

    @app.post(
        "/v1/chat/completions",
        dependencies=[Depends(verify_bearer)],
    )
    async def create_chat_completions(
        payload: ChatCompletionRequest,
        service: Any = Depends(get_gateway_service),
    ) -> Any:
        if payload.stream:
            return StreamingResponse(
                service.stream_chat_completion(payload),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        return await service.create_chat_completion(payload)

    @app.post(
        "/chat/completions",
        dependencies=[Depends(verify_bearer)],
    )
    async def create_chat_completions_alias(
        payload: ChatCompletionRequest,
        service: Any = Depends(get_gateway_service),
    ) -> Any:
        if payload.stream:
            return StreamingResponse(
                service.stream_chat_completion(payload),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        return await service.create_chat_completion(payload)

    return app


def main() -> None:
    import uvicorn

    settings = GatewaySettings()
    app = create_app(settings=settings)
    print(f"Base URL: http://{settings.host}:{settings.port}/v1")
    print(f"API Key: {settings.api_key}")
    print(f"Default model: {settings.default_model}")
    print(f"Default reasoning effort: {settings.default_reasoning_effort}")
    account_mode = "unknown"
    try:
        service = getattr(app.state, "gateway_service", None)
        snapshot = service.get_account_snapshot() if service is not None else None
        if snapshot is not None and isinstance(getattr(snapshot, "mode", None), str):
            account_mode = snapshot.mode
    except Exception:
        account_mode = "unavailable"
    print(f"Account mode: {account_mode}")
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
