"""LINE Messaging API：簽章驗證與回覆訊息。"""
import base64
import hashlib
import hmac
import logging

import httpx

logger = logging.getLogger(__name__)

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
MULTICAST_URL = "https://api.line.me/v2/bot/message/multicast"
PUSH_URL = "https://api.line.me/v2/bot/message/push"
BOT_INFO_URL = "https://api.line.me/v2/bot/info"
_MAX_TEXT_LENGTH = 4900
_MULTICAST_MAX_RECIPIENTS = 500


def verify_signature(channel_secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = base64.b64encode(hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


def _raise_with_detail(response: httpx.Response, context: str) -> None:
    """LINE 的 4xx 會附上哪個欄位不合法；不記下來就只能瞎猜。"""
    if response.status_code >= 400:
        logger.error("LINE %s 失敗 %s %s", context, response.status_code, response.text[:500])
    response.raise_for_status()


class LineClient:
    def __init__(self, http: httpx.AsyncClient, access_token: str):
        self._http = http
        self._access_token = access_token

    @staticmethod
    def _payload(message: str | dict) -> dict:
        """字串包成 text message；dict 視為完整 message 物件（如 Flex）。"""
        if isinstance(message, str):
            return {"type": "text", "text": message[:_MAX_TEXT_LENGTH]}
        return message

    async def reply(self, reply_token: str, message: str | dict) -> None:
        payload = self._payload(message)
        response = await self._http.post(
            REPLY_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
            json={"replyToken": reply_token, "messages": [payload]},
        )
        _raise_with_detail(response, "reply")

    async def push(self, user_id: str, message: str | dict) -> None:
        """主動推播給單一使用者（週報等個人化訊息）。"""
        response = await self._http.post(
            PUSH_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
            json={"to": user_id, "messages": [self._payload(message)]},
        )
        _raise_with_detail(response, "push")

    async def bot_info(self) -> dict:
        """取得官方帳號自身資訊（basicId、displayName 等）。"""
        response = await self._http.get(
            BOT_INFO_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        _raise_with_detail(response, "bot_info")
        return response.json()

    async def multicast(self, user_ids: list[str], messages: str | dict | list) -> None:
        """主動推播給多位使用者；可一次帶多則訊息（LINE 上限 5 則）。"""
        if not isinstance(messages, list):
            messages = [messages]
        response = await self._http.post(
            MULTICAST_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
            json={
                "to": user_ids[:_MULTICAST_MAX_RECIPIENTS],
                "messages": [self._payload(m) for m in messages[:5]],
            },
        )
        _raise_with_detail(response, "multicast")
