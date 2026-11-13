"""Selected-only loading; bundled providers never depend on entry-point metadata."""

from importlib import import_module, metadata
import re
import sys

from decision_cli.providers import API_VERSION, ENTRY_POINT_GROUP, ProviderInfo

BUILTINS = {
    "typesafe": "decision_cli.typesafe_provider",
    "openai": "decision_cli.openai_provider",
    "cloudflare": "decision_cli.cloudflare_provider",
    "gemini": "decision_cli.gemini_provider",
}
BUILTIN_CREDENTIALS = {
    "TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY", "OPENAI_API_KEY",
    "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY",
}


def load_provider(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("Provider names must use lowercase letters, digits, underscores, or hyphens")
    try:
        if name in BUILTINS:
            provider = import_module(BUILTINS[name]).PROVIDER
        else:
            if getattr(sys, "frozen", False):
                raise ValueError("Standalone executables support built-in providers only; install decision with Python to use provider plugins")
            entries = metadata.entry_points(group=ENTRY_POINT_GROUP, name=name)
            if not entries:
                raise ValueError("Unknown provider; choose typesafe, openai, cloudflare, gemini, or install a provider plugin in the same Python environment")
            if len(entries) != 1:
                raise ValueError("Multiple installed plugins register this provider; remove the duplicate registration")
            try:
                provider = next(iter(entries)).load()
            except Exception:
                raise ValueError("Selected provider plugin could not be loaded") from None
        if (provider.api_version != API_VERSION or not isinstance(provider.info, ProviderInfo)
                or provider.info.name != name
                or not all(callable(getattr(provider, method, None)) for method in
                           ("prepare", "validate_connection", "evaluate", "schema_constraints"))):
            raise ValueError("Provider plugin must implement API version 1 and match its registered name")
        info = provider.info
        if not isinstance(info.credential_env_vars, tuple):
            raise ValueError("Provider credential environment-variable names must be a tuple")
        variables = (*info.credential_env_vars, *((info.base_url_env,) if info.base_url_env else ()))
        if (not isinstance(info.default_base_url, str) or not info.default_base_url
                or (info.default_model is not None and (not isinstance(info.default_model, str) or not info.default_model.strip()))
                or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in variables)):
            raise ValueError("Provider plugin has invalid defaults or environment-variable names")
        if name not in BUILTINS and any(value.startswith("DECISION_") or value in BUILTIN_CREDENTIALS
                                        for value in info.credential_env_vars):
            raise ValueError("Provider plugins cannot use core or built-in credential names as implicit fallbacks")
        return provider
    except (AttributeError, TypeError):
        raise ValueError("Provider plugin must implement API version 1 and match its registered name") from None
