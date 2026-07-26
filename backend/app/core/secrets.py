"""Secret provider abstraction (C-09: secrets fetched at runtime, never hard-coded)."""

import os
from typing import Protocol

from app.config import Settings
from app.core.errors import DependencyUnavailable


class SecretProvider(Protocol):
    def get(self, name: str) -> str:
        """Return the secret value for `name`, or raise if unavailable."""
        ...


class EnvSecretProvider:
    """Default dev provider: reads secrets from process environment variables."""

    def get(self, name: str) -> str:
        try:
            return os.environ[name]
        except KeyError:
            raise DependencyUnavailable(
                f"secret '{name}' not found in environment"
            ) from None


class CyberArkSecretProvider:
    """Placeholder for the CyberArk integration (raises until configured).

    Intended production flow (design doc §5.5 / §6):
    1. Authenticate the application to CyberArk using the app's machine
       credential — certificate-based PVWA app logon [TBD-04].
    2. Retrieve account secrets via the Central Credential Provider:
       GET /AIMWebService/api/Accounts?AppID=...&Safe=...&Object=...
       (or PVWA REST /PasswordVault/API/Accounts/{id}/Password/Retrieve).
    3. Hold secrets in request-scoped memory only — never logged or persisted;
       refresh from CCP before expiry.
    4. Fail closed with 503 (DependencyUnavailable) when CyberArk is
       unreachable, at startup and on demand.
    """

    def get(self, name: str) -> str:
        raise DependencyUnavailable("CyberArk provider not configured")


def get_secret_provider(settings: Settings) -> SecretProvider:
    if settings.SECRET_PROVIDER == "env":
        return EnvSecretProvider()
    if settings.SECRET_PROVIDER == "cyberark":
        return CyberArkSecretProvider()
    raise ValueError(f"unknown SECRET_PROVIDER: {settings.SECRET_PROVIDER}")
