"""Version 1 API for trusted, independently installed decision providers."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from decision_cli.errors import ProviderError

API_VERSION = 1
ENTRY_POINT_GROUP = "decision.providers.v1"
JSONObject = dict[str, Any]


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    default_base_url: str
    default_model: str | None = None
    base_url_env: str | None = None
    credential_env_vars: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectionSettings:
    base_url: str
    timeout: float
    account_id: str | None = None


@dataclass(frozen=True)
class PreparedRequest:
    request: JSONObject
    data: object


class Provider(Protocol):
    api_version: int
    info: ProviderInfo

    def prepare(self, request: JSONObject, images: Mapping[str, bytes]) -> PreparedRequest:
        """Normalize model and validate/encode locally; no settings or credentials.

        Request contains state, questions, and optional model, never assets.
        Preserve state/questions. data is opaque transport input owned by the
        provider. Raise ValueError with a locally written input-error message.
        """
        ...

    def validate_connection(self, prepared: PreparedRequest, settings: ConnectionSettings) -> None:
        """Validate execution-only settings locally. Not called for dry runs."""
        ...

    def evaluate(self, prepared: PreparedRequest, settings: ConnectionSettings, api_key: str) -> JSONObject:
        """Send one batch without retries/redirects; return model, answers, usage.

        Core validates common results and owns duration_ms. Raise ProviderError
        only with a locally written message containing no remote body or secret.
        """
        ...

    def schema_constraints(self) -> JSONObject:
        """Return pure JSON Schema restrictions combined with the common schema.

        Runtime validation in prepare remains authoritative. No network, settings,
        or credentials; use {} if there are no additional restrictions.
        """
        ...
