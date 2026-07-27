import httpx

from app.auth.oauth import get_access_token
from app.config.settings import settings


class CentralSquareAPIError(Exception):
    """Raised when a CentralSquare API request fails."""


class CentralSquareClient:
    def __init__(self):
        self.token = get_access_token()

    def headers(self) -> dict:
        return {
            "accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "From": settings.from_header,
        }

    def get_system_config(self, configuration: str) -> dict:
        url = f"{settings.system_base_url}/configurations"
        params = {"configuration": configuration}
        return self.get(url, params=params)

    def search_cfs_core(
        self,
        search_body: dict,
        skip: int = 0,
        limit: int = 100,
    ) -> dict:
        url = f"{settings.cad_base_url}/cfs_core/search"
        params = {
            "skip": max(skip, 0),
            "limit": min(max(limit, 1), 100),
        }
        return self.post(url, json=search_body, params=params)

    def search_units(
        self,
        search_body: dict | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> dict:
        url = f"{settings.cad_base_url}/units/search"
        params = {
            "skip": max(skip, 0),
            "limit": min(max(limit, 1), 100),
        }
        return self.post(url, json=search_body or {}, params=params)

    def get_cfs_core(self, cfs_number: str) -> dict:
        url = f"{settings.cad_base_url}/cfs_core/{cfs_number}"
        return self.get(url)

    def get_cfs_analytics(self, cfs_number: str) -> dict:
        url = f"{settings.cad_base_url}/cfs_analytics/{cfs_number}"
        return self.get(url)

    def run_command(self, command_body: dict) -> dict:
        url = f"{settings.cad_base_url}/run_command"
        return self.post(url, json=command_body)

    def get(self, url: str, params: dict | None = None) -> dict:
        try:
            response = httpx.get(
                url,
                headers=self.headers(),
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as exc:
            raise CentralSquareAPIError(
                f"GET request failed: {exc}"
            ) from exc

    def post(
        self,
        url: str,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        try:
            response = httpx.post(
                url,
                headers=self.headers(),
                json=json or {},
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as exc:
            raise CentralSquareAPIError(
                f"POST request failed: {exc}"
            ) from exc
