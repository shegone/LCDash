import httpx
from app.config.settings import settings


class CentralSquareAuthError(Exception):
    """Raised when CentralSquare authentication fails."""


def get_access_token() -> str:
    """
    Authenticate with CentralSquare and return a bearer access token.
    """

    if not settings.username or not settings.password:
        raise CentralSquareAuthError(
            "CentralSquare username or password is missing from .env"
        )

    data = {
        "username": settings.username,
        "password": settings.password,
    }

    headers = {
        "accept": "application/json",
    }

    try:
        response = httpx.post(
            settings.token_url,
            data=data,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

    except httpx.HTTPError as exc:
        raise CentralSquareAuthError(
            f"CentralSquare authentication request failed: {exc}"
        ) from exc

    token_data = response.json()
    access_token = token_data.get("access_token")

    if not access_token:
        raise CentralSquareAuthError(
            "CentralSquare did not return an access_token."
        )

    return access_token