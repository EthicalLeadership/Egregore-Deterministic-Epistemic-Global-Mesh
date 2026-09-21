"""Compatibility stub for API key middleware.

Provides every symbol expected by the Egregore bootstrap without
enforcing authentication. Local access is trusted.
"""

_API_KEYS = []


class ApiKeyMiddleware:
    """No-op API key middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


def is_valid_api_key(api_key: str) -> bool:
    return False


def get_identity_for_key(api_key: str):
    return None


def get_user_identity(api_key: str = ""):
    return None


def get_identity(api_key: str = ""):
    return None


def _hash_key(api_key: str) -> str:
    return ""
