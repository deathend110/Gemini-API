import unittest
import sys
import types
from unittest.mock import patch

gemini_stub = types.ModuleType("gemini_webapi")
gemini_stub.GeminiClient = object

exceptions_stub = types.ModuleType("gemini_webapi.exceptions")


class _GeminiStubError(Exception):
    pass


exceptions_stub.APIError = _GeminiStubError
exceptions_stub.AuthError = _GeminiStubError
exceptions_stub.GeminiError = _GeminiStubError
exceptions_stub.TimeoutError = _GeminiStubError

sys.modules.setdefault("gemini_webapi", gemini_stub)
sys.modules.setdefault("gemini_webapi.exceptions", exceptions_stub)

from gateway.account import (
    GatewayAccountSnapshot,
    evaluate_account_mode,
    validate_required_account_level,
)
from gateway.config import GatewaySettings


class TestGatewayAccountStatus(unittest.TestCase):
    def test_full_capability_maps_to_available(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="AVAILABLE",
            raw_account_status_code=1000,
            chat_available=True,
            advanced_models_available=True,
            deep_research_available=True,
            full_web_capability_available=True,
            mode="unknown",
            unavailable_reasons=[],
        )

        evaluated = evaluate_account_mode(snapshot)

        self.assertEqual(evaluated.mode, "available")

    def test_chat_only_maps_to_degraded(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="UNAUTHENTICATED",
            raw_account_status_code=1016,
            chat_available=True,
            advanced_models_available=False,
            deep_research_available=False,
            full_web_capability_available=False,
            mode="unknown",
            unavailable_reasons=["advanced_models_unavailable"],
        )

        evaluated = evaluate_account_mode(snapshot)

        self.assertEqual(evaluated.mode, "degraded")

    def test_required_full_web_raises_when_not_satisfied(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="UNAUTHENTICATED",
            raw_account_status_code=1016,
            chat_available=True,
            advanced_models_available=True,
            deep_research_available=False,
            full_web_capability_available=False,
            mode="degraded",
            unavailable_reasons=["deep_research_unavailable"],
        )

        with self.assertRaisesRegex(ValueError, "full_web"):
            validate_required_account_level(snapshot, "full_web")

    def test_required_full_web_depends_only_on_full_web_capability(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="LIMITED",
            raw_account_status_code=1001,
            chat_available=True,
            advanced_models_available=False,
            deep_research_available=False,
            full_web_capability_available=True,
            mode="degraded",
            unavailable_reasons=["advanced_models_unavailable"],
        )

        validate_required_account_level(snapshot, "full_web")

    def test_required_standard_accepts_chat_with_advanced_models(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="AVAILABLE",
            raw_account_status_code=1000,
            chat_available=True,
            advanced_models_available=True,
            deep_research_available=False,
            full_web_capability_available=False,
            mode="degraded",
            unavailable_reasons=["deep_research_unavailable"],
        )

        validate_required_account_level(snapshot, "standard")

    def test_required_standard_rejects_missing_advanced_models(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="LIMITED",
            raw_account_status_code=1001,
            chat_available=True,
            advanced_models_available=False,
            deep_research_available=True,
            full_web_capability_available=True,
            mode="available",
            unavailable_reasons=["advanced_models_unavailable"],
        )

        with self.assertRaisesRegex(ValueError, "standard"):
            validate_required_account_level(snapshot, "standard")

    def test_gateway_settings_exposes_v12_account_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = GatewaySettings()

        self.assertTrue(settings.cookie_persist_enabled)
        self.assertEqual(settings.cookie_persist_interval_seconds, 60)
        self.assertTrue(settings.account_probe_enabled)
        self.assertFalse(settings.account_strict_mode)
        self.assertEqual(settings.account_required_level, "basic")

    def test_gateway_settings_normalizes_account_required_level(self) -> None:
        with patch.dict(
            "os.environ",
            {"GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL": " Full_Web "},
            clear=True,
        ):
            settings = GatewaySettings()

        self.assertEqual(settings.account_required_level, "full_web")

    def test_gateway_settings_rejects_invalid_account_required_level(self) -> None:
        with patch.dict(
            "os.environ",
            {"GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL": "premium"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "premium"):
                GatewaySettings()

    def test_gateway_settings_rejects_non_positive_cookie_persist_interval(self) -> None:
        for invalid_value in ("0", "-5"):
            with self.subTest(invalid_value=invalid_value):
                with patch.dict(
                    "os.environ",
                    {
                        "GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS": invalid_value,
                    },
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS",
                    ):
                        GatewaySettings()


if __name__ == "__main__":
    unittest.main()
