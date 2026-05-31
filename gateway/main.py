from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from gateway.auth import build_bearer_verifier
from gateway.config import GatewaySettings
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse, ModelListResponse
from gateway.service import GatewayService, GatewayServiceError


def get_gateway_service(request: Request) -> Any:
    return request.app.state.gateway_service


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    resolved_settings = settings or GatewaySettings()
    verify_bearer = build_bearer_verifier(resolved_settings)

    app = FastAPI(title="Gemini OpenAI Gateway", version="1.0.0")

    app.state.gateway_service = GatewayService(resolved_settings)

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

    @app.post(
        "/v1/chat/completions",
        dependencies=[Depends(verify_bearer)],
        response_model=ChatCompletionResponse,
    )
    async def create_chat_completions(
        payload: ChatCompletionRequest,
        service: Any = Depends(get_gateway_service),
    ) -> Any:
        return await service.create_chat_completion(payload)

    @app.post(
        "/chat/completions",
        dependencies=[Depends(verify_bearer)],
        response_model=ChatCompletionResponse,
    )
    async def create_chat_completions_alias(
        payload: ChatCompletionRequest,
        service: Any = Depends(get_gateway_service),
    ) -> Any:
        return await service.create_chat_completion(payload)

    return app
