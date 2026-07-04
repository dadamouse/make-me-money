"""LINE Messaging API：簽章驗證與回覆訊息。"""
import base64
import hashlib
import hmac

import httpx

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
MULTICAST_URL = "https://api.line.me/v2/bot/message/multicast"
_MAX_TEXT_LENGTH = 4900
_MULTICAST_MAX_RECIPIENTS = 500


def verify_signature(channel_secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = base64.b64encode(hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


class LineClient:
    def __init__(self, http: httpx.AsyncClient, access_token: str):
        self._http = http
        self._access_token = access_token

    async def reply(self, reply_token: str, message: str | dict) -> None:
        """message 為字串時包成 text message，為 dict 時視為完整 message 物件（如 Flex）。"""
        if isinstance(message, str):
            payload = {"type": "text", "text": message[:_MAX_TEXT_LENGTH]}
        else:
            payload = message
        response = await self._http.post(
            REPLY_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
            json={"replyToken": reply_token, "messages": [payload]},
        )
        response.raise_for_status()

    async def multicast(self, user_ids: list[str], text: str) -> None:
        """主動推播文字訊息給多位使用者（每日選股用）。"""
        response = await self._http.post(
            MULTICAST_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
            json={
                "to": user_ids[:_MULTICAST_MAX_RECIPIENTS],
                "messages": [{"type": "text", "text": text[:_MAX_TEXT_LENGTH]}],
            },
        )
        response.raise_for_status()
