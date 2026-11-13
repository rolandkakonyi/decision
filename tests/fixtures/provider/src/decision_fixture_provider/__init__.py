"""Separately packaged provider for local transport conformance tests only."""

import base64
import json

from decision_cli.providers import PreparedRequest, ProviderError, ProviderInfo


class FixtureProvider:
    api_version = 1
    info = ProviderInfo("fixture", "https://fixture.example/api", "fixture-default",
                        "FIXTURE_BASE_URL", ("FIXTURE_API_KEY",))

    def prepare(self, request, images):
        if len(request["questions"]) > 2 or any(q["type"] != "noul" for q in request["questions"].values()):
            raise ValueError("Fixture accepts at most two Noul questions")
        return PreparedRequest(request, json.dumps({**request, "images": {
            key: base64.b64encode(value).decode("ascii") for key, value in images.items()
        }}, allow_nan=False).encode())

    def validate_connection(self, prepared, settings):
        if settings.account_id == "invalid":
            raise ValueError("Fixture account is invalid")

    def evaluate(self, prepared, settings, api_key):
        import httpx2

        with httpx2.Client(timeout=settings.timeout, follow_redirects=False) as client:
            response = client.post(settings.base_url + "/evaluate", content=prepared.data,
                                   headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
        if not 200 <= response.status_code < 300:
            raise ProviderError("Fixture request failed; no answers returned")
        return response.json()

    def schema_constraints(self):
        return {"properties": {"questions": {"maxProperties": 2, "additionalProperties": {
            "properties": {"type": {"const": "noul"}}
        }}}}


PROVIDER = FixtureProvider()
