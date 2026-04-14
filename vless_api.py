import httpx


class VlessApiClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None

    async def get_token(self) -> str:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/admin/token",
                data={"username": self.username, "password": self.password},
            )
            r.raise_for_status()
            self._token = r.json()["access_token"]
            return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def _ensure_token(self):
        if not self._token:
            await self.get_token()

    async def create_user(self, username: str, expire_timestamp: int, data_limit_gb: int = 0) -> dict:
        await self._ensure_token()
        payload = {
            "username": username,
            "proxies": {"vless": {"flow": "xtls-rprx-vision"}},
            "expire": expire_timestamp,
            "data_limit": data_limit_gb * 1024**3,
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}/api/user",
                json=payload,
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def get_user(self, username: str) -> dict:
        await self._ensure_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/api/user/{username}",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def delete_user(self, username: str) -> bool:
        await self._ensure_token()
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{self.base_url}/api/user/{username}",
                headers=self._headers(),
            )
            return r.status_code == 200

    async def update_user_expire(self, username: str, expire_timestamp: int) -> dict:
        await self._ensure_token()
        async with httpx.AsyncClient() as client:
            r = await client.put(
                f"{self.base_url}/api/user/{username}",
                json={"expire": expire_timestamp},
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def disable_user(self, username: str) -> bool:
        await self._ensure_token()
        async with httpx.AsyncClient() as client:
            r = await client.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": "disabled"},
                headers=self._headers(),
            )
            return r.status_code == 200

    async def enable_user(self, username: str) -> bool:
        await self._ensure_token()
        async with httpx.AsyncClient() as client:
            r = await client.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": "active"},
                headers=self._headers(),
            )
            return r.status_code == 200

    async def get_user_stats(self, username: str) -> dict:
        return await self.get_user(username)


def get_subscription_url(api_url: str, username: str) -> str:
    from config import settings
    return f"{settings.site_url}/sub/{username}"
