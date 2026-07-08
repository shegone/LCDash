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

    def search_cfs_core(self, search_body: dict) -> dict:
        url = f"{settings.cad_base_url}/cfs_core/search"
        return self.post(url, json=search_body)

    def get_cfs_core(self, cfs_number: str) -> dict:
        url = f"{settings.cad_base_url}/cfs_core/{cfs_number}"
        return self.get(url)

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

    def post(self, url: str, json: dict | None = None) -> dict:
        try:
            response = httpx.post(
                url,
                headers=self.headers(),
                json=json or {},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as exc:
            raise CentralSquareAPIError(
                f"POST request failed: {exc}"
            ) from exc