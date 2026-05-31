from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from gateway.config import GatewaySettings


def build_bearer_verifier(settings: GatewaySettings):
    def verify_bearer(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = f"Bearer {settings.api_key}"
        if authorization != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="unauthorized",
            )

    return verify_bearer
