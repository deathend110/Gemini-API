from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

ACCOUNT_REQUIRED_LEVELS = {"basic", "standard", "full_web"}


@dataclass(frozen=True)
class GatewayAccountSnapshot:
    raw_account_status: str
    raw_account_status_code: int | None
    chat_available: bool
    advanced_models_available: bool
    deep_research_available: bool
    full_web_capability_available: bool
    mode: str = "unknown"
    unavailable_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_account_mode(snapshot: GatewayAccountSnapshot) -> GatewayAccountSnapshot:
    if snapshot.chat_available and snapshot.full_web_capability_available:
        mode = "available"
    elif snapshot.chat_available:
        mode = "degraded"
    else:
        mode = "blocked"

    return replace(snapshot, mode=mode)


def validate_required_account_level(
    snapshot: GatewayAccountSnapshot,
    required_level: str,
) -> None:
    normalized_level = required_level.strip().lower()
    if normalized_level not in ACCOUNT_REQUIRED_LEVELS:
        raise ValueError(f"Unsupported required account level: {required_level}")

    if normalized_level == "basic":
        if snapshot.chat_available:
            return
    elif normalized_level == "standard":
        if snapshot.chat_available and snapshot.advanced_models_available:
            return
    elif normalized_level == "full_web":
        if snapshot.full_web_capability_available:
            return

    raise ValueError(
        "Gateway account snapshot does not satisfy required level: "
        f"{required_level}"
    )


__all__ = [
    "ACCOUNT_REQUIRED_LEVELS",
    "GatewayAccountSnapshot",
    "evaluate_account_mode",
    "validate_required_account_level",
]
