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

        params = {
            "configuration": configuration
        }

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
                f"System configuration request failed: {exc}"
            ) from exc