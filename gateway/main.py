from __future__ import annotations

from fastapi import Depends, FastAPI

from gateway.auth import build_bearer_verifier
from gateway.config import GatewaySettings


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    resolved_settings = settings or GatewaySettings()
    verify_bearer = build_bearer_verifier(resolved_settings)

    app = FastAPI(title="Gemini OpenAI Gateway", version="1.0.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models", dependencies=[Depends(verify_bearer)])
    def list_models() -> dict[str, list]:
        return {"object": "list", "data": []}

    return app
