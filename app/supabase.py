"""Supabase PostgREST 極簡 client。"""
import httpx


class SupabaseClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, service_key: str):
        self._http = http
        self._base_url = base_url
        self._service_key = service_key

    async def request(
        self,
        method: str,
        path_and_query: str,
        json_body: list | dict | None = None,
        prefer: str = "return=representation",
    ) -> list:
        response = await self._http.request(
            method,
            f"{self._base_url}/rest/v1/{path_and_query}",
            json=json_body,
            headers={
                "apikey": self._service_key,
                "Authorization": f"Bearer {self._service_key}",
                "Prefer": prefer,
            },
        )
        response.raise_for_status()
        return response.json() if response.content else []

    async def get(self, path_and_query: str) -> list:
        return await self.request("GET", path_and_query)

    async def insert(self, path_and_query: str, body: list | dict, prefer: str = "return=representation") -> list:
        return await self.request("POST", path_and_query, json_body=body, prefer=prefer)

    async def patch(self, path_and_query: str, body: dict) -> list:
        return await self.request("PATCH", path_and_query, json_body=body)

    async def delete(self, path_and_query: str) -> list:
        return await self.request("DELETE", path_and_query)

    async def rpc(self, function_name: str, args: dict) -> list:
        return await self.request("POST", f"rpc/{function_name}", json_body=args)
