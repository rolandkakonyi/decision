"""Bundled TypeSafe provider using the same interface as external providers."""

import logging

from decision_cli.providers import PreparedRequest, ProviderInfo


class TypeSafeProvider:
    api_version = 1
    info = ProviderInfo("typesafe", "https://api.typesafe.ai", base_url_env="TYPESAFE_BASE_URL",
                        credential_env_vars=("TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY"))

    def prepare(self, request, images):
        return PreparedRequest(request, images)

    def validate_connection(self, prepared, settings):
        pass

    def evaluate(self, prepared, settings, api_key):
        from typesafe_sdk import Image, RetryPolicy, TypeSafeClient

        logging.getLogger("typesafe_sdk").setLevel(logging.CRITICAL)
        options = {}
        if prepared.data:
            options["assets"] = {name: Image(data) for name, data in prepared.data.items()}
        if prepared.data or any(q["type"] == "box" for q in prepared.request["questions"].values()):
            options["extra_headers"] = {"TypeSafe-Danger-Zone-Images": "true"}
        with TypeSafeClient(api_key=api_key, base_url=settings.base_url, timeout=settings.timeout,
                            retry=RetryPolicy(max_retries=0), compression=False) as client:
            response = client.system_one(**prepared.request, **options)
        return response.model_dump(mode="json", warnings=False)

    def schema_constraints(self):
        return {}


PROVIDER = TypeSafeProvider()
