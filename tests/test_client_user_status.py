import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orjson as json

import gemini_webapi.client as client_module
from gemini_webapi import GeminiClient
from gemini_webapi.constants import AccountStatus, Model


def build_get_user_status_response(
    *,
    status_code: int,
) -> SimpleNamespace:
    body = [None] * 18
    body[14] = status_code
    body[15] = [
        [
            Model.BASIC_FLASH.model_id,
            "Flash",
            "All-around help",
        ],
        [
            Model.BASIC_PRO.model_id,
            "Pro",
            "Advanced math & code",
        ],
    ]
    body[16] = []
    body[17] = []
    envelope = [[None, None, json.dumps(body).decode("utf-8")]]
    return SimpleNamespace(text=json.dumps(envelope).decode("utf-8"))


class TestClientUserStatus(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_user_status_treats_1016_as_neutral_limited_session(self) -> None:
        client = GeminiClient()
        response = build_get_user_status_response(status_code=1016)

        with patch.object(
            client,
            "_batch_execute",
            AsyncMock(return_value=response),
        ), patch.object(client_module, "logger") as logger_mock:
            await client._fetch_user_status()

        self.assertEqual(client.account_status, AccountStatus.SESSION_UNVERIFIED)
        self.assertEqual(
            [model.model_name for model in client.list_models() or []],
            ["gemini-3-flash", "gemini-3-pro"],
        )
        self.assertTrue(
            all(model.is_available for model in client.list_models() or [])
        )
        logger_mock.warning.assert_not_called()
        logger_mock.info.assert_called_once()
        info_message = logger_mock.info.call_args.args[0]
        self.assertIn("SESSION_UNVERIFIED", info_message)
        self.assertNotIn("cookies have expired", info_message)


if __name__ == "__main__":
    unittest.main()
