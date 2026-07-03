"""LINE Messaging API：簽章驗證與回覆訊息。"""
import base64
import hashlib
import hmac

import httpx

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
_MAX_TEXT_LENGTH = 4900


def verify_signature(channel_secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = base64.b64encode(hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


class LineClient:
    def __init__(self, http: httpx.AsyncClient, access_token: str):
        self._http = http
        self._access_token = access_token

    async def reply(self, reply_token: str, text: str) -> None:
        response = await self._http.post(
            REPLY_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": str(text)[:_MAX_TEXT_LENGTH]}]},
        )
        response.raise_for_status()
