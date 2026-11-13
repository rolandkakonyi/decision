"""Provider discovery and core policy; no network or paid inference."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from importlib.metadata import EntryPoint
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from decision_cli import cli, registry
from decision_cli.providers import ENTRY_POINT_GROUP, PreparedRequest, ProviderError, ProviderInfo

REQUEST = {"state": "context", "questions": {"q": {"type": "noul", "instructions": "True?"}}}


def provider():
    return SimpleNamespace(
        api_version=1,
        info=ProviderInfo("fixture", "https://fixture.example/api", "default", "FIXTURE_BASE_URL", ("FIXTURE_API_KEY",)),
        prepare=Mock(side_effect=lambda request, images: PreparedRequest(request, images)),
        validate_connection=Mock(),
        evaluate=Mock(return_value={"model": "default", "answers": {"q": {"type": "noul", "noul": .5}},
                                   "usage": {"input_tokens": 0, "output_tokens": None}, "duration_ms": 999999}),
        schema_constraints=Mock(return_value={}),
    )


class ProviderPluginTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def args(self, *flags):
        return cli.parser().parse_args(["noul", "True?", "--state", "context", "--provider", "fixture", *flags])

    def test_load_only_selected_and_reject_ambiguous_or_invalid_registration(self):
        selected = Mock(name="selected", load=Mock(return_value=provider()))
        with patch.object(registry.metadata, "entry_points", return_value=(selected,)) as discover:
            self.assertEqual(registry.load_provider("fixture").info.name, "fixture")
            discover.assert_called_once_with(group=ENTRY_POINT_GROUP, name="fixture")
            selected.load.assert_called_once_with()
        for entries in ((), (selected, selected)):
            with patch.object(registry.metadata, "entry_points", return_value=entries), self.assertRaises(ValueError):
                registry.load_provider("fixture")
        for change in ({"api_version": 2}, {"info": replace(provider().info, name="other")}, {"prepare": None}):
            invalid = provider()
            invalid.__dict__.update(change)
            with patch.object(registry.metadata, "entry_points", return_value=(Mock(load=Mock(return_value=invalid)),)), self.assertRaises(ValueError):
                registry.load_provider("fixture")
        with patch.object(registry.metadata, "entry_points", return_value=(EntryPoint("broken", "missing_decision:provider", ENTRY_POINT_GROUP),)):
            with self.assertRaisesRegex(ValueError, "could not be loaded") as error:
                registry.load_provider("broken")
            self.assertNotIn("missing_decision", str(error.exception))

    def test_builtins_help_and_exports_never_discover_external_plugins(self):
        with patch.object(registry.metadata, "entry_points", side_effect=AssertionError("must not discover")):
            self.assertEqual(registry.load_provider("typesafe").info.name, "typesafe")
            for command in (["--help"], ["--version"], ["skill"], ["noul", "--help"]):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    try:
                        self.assertEqual(cli.main(command), 0)
                    except SystemExit as error:
                        self.assertEqual(error.code, 0)

    def test_frozen_external_selection_explains_python_requirement(self):
        with patch.object(registry.sys, "frozen", True, create=True), patch.object(registry.metadata, "entry_points") as discover:
            with self.assertRaisesRegex(ValueError, "install decision with Python"):
                registry.load_provider("fixture")
            discover.assert_not_called()

    def test_disabled_provider_fails_before_loading_for_flags_environment_and_mcp(self):
        for command in (["noul", "True?", "--state", "x", "--provider", "fixture"],
                        ["noul", "True?", "--state", "x", "--dry-run"],
                        ["mcp", "--provider", "fixture"]):
            with self.subTest(command=command), patch.dict(os.environ, {"DECISION_PROVIDER": "fixture"}), \
                    patch.object(registry, "load_provider") as load, \
                    redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                code = cli.main([*command, "--disable-provider", "other", "--disable-provider", "fixture"])
                self.assertEqual((code, out.getvalue()), (2, ""))
                self.assertIn("disabled for this invocation", err.getvalue())
                load.assert_not_called()
        args = self.args()
        with patch.object(registry, "load_provider", return_value=provider()) as load:
            cli.selected_provider(args)
            args.disable_provider.append("fixture")
            with self.assertRaisesRegex(ValueError, "disabled for this invocation"):
                cli.selected_provider(args)
            self.assertEqual(load.call_count, 1)

    def test_disabling_other_names_keeps_selected_builtin_and_extension_available(self):
        for name in ("typesafe", "fixture"):
            args = self.args("--provider", name, "--disable-provider", "other", "--disable-provider", "broken")
            expected = registry.load_provider("typesafe") if name == "typesafe" else provider()
            with patch.object(registry, "load_provider", return_value=expected):
                self.assertIs(cli.selected_provider(args), expected)
        args = self.args("--provider", "typesafe", "--disable-provider", "typesafe")
        with patch.object(registry, "load_provider") as load, self.assertRaises(ValueError):
            cli.selected_provider(args)
        load.assert_not_called()

    def test_external_credentials_are_host_scoped_and_selector_is_exclusive(self):
        plugin = provider()
        with patch.object(registry, "load_provider", return_value=plugin), patch.dict(os.environ, {"FIXTURE_API_KEY": "fixture-key", "OPENAI_API_KEY": "other-key"}):
            args = self.args()
            self.assertEqual(cli.connection(args), "fixture-key")
            for url in ("https://custom.example", "https://fixture.example.evil", "https://fixture.example:444", "https://api.openai.com/v1"):
                args.base_url = url
                self.assertIsNone(cli.connection(args))
            args.base_url = plugin.info.default_base_url
            args.api_key_env = "UNSET"
            self.assertIsNone(cli.connection(args))
            os.environ["DECISION_API_KEY"] = "generic-key"
            self.assertIsNone(cli.connection(args))
            args.api_key_env = None
            self.assertEqual(cli.connection(args), "generic-key")
        for name in ("OPENAI_API_KEY", "DECISION_MODEL", "DECISION_API_KEY", "GOOGLE_API_KEY"):
            invalid = provider()
            invalid.info = replace(invalid.info, credential_env_vars=(name,))
            with patch.object(registry.metadata, "entry_points", return_value=(Mock(load=Mock(return_value=invalid)),)), self.assertRaises(ValueError):
                registry.load_provider("fixture")

    def test_preparation_and_connection_validation_precede_timing_and_execution(self):
        for stage in ("prepare", "validate_connection"):
            plugin = provider()
            getattr(plugin, stage).side_effect = ValueError("Local input error")
            with patch.object(registry, "load_provider", return_value=plugin), patch.dict(os.environ, {"DECISION_API_KEY": "fixture"}), patch.object(cli.time, "perf_counter") as clock:
                with self.assertRaisesRegex(ValueError, "Local input error"):
                    cli.evaluate_request(REQUEST, self.args())
                clock.assert_not_called()
                plugin.evaluate.assert_not_called()
        plugin = provider()
        with patch.object(registry, "load_provider", return_value=plugin), patch.object(cli.time, "perf_counter") as clock:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["noul", "True?", "--state", "context", "--provider", "fixture", "--dry-run"]), 0)
            plugin.validate_connection.assert_not_called()
            plugin.evaluate.assert_not_called()
            clock.assert_not_called()

    def test_cloudflare_dry_run_needs_neither_credentials_nor_account(self):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = cli.main(["noul", "True?", "--state", "context", "--provider", "cloudflare",
                             "--model", "@cf/cloudflare/clef-flash", "--dry-run", "--json"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn('"model": "clef-flash"', out.getvalue())

    def test_core_validates_external_results_and_owns_duration(self):
        plugin = provider()
        with patch.object(registry, "load_provider", return_value=plugin), patch.dict(os.environ, {"DECISION_API_KEY": "fixture"}), patch.object(cli.time, "perf_counter", side_effect=[1, 1.125]):
            result = cli.evaluate_request(REQUEST, self.args())
        self.assertEqual(result["duration_ms"], 125)
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": None})
        for value in (2, "private response", True, float("nan")):
            plugin.evaluate.return_value = {"model": "m", "answers": {"q": {"type": "noul", "noul": value}}}
            with patch.object(registry, "load_provider", return_value=plugin), patch.dict(os.environ, {"DECISION_API_KEY": "fixture"}), self.assertRaises(ProviderError) as error:
                cli.evaluate_request(REQUEST, self.args())
            self.assertNotIn("private response", str(error.exception))
        plugin.evaluate.side_effect = RuntimeError("fixture-key private response")
        with patch.object(registry, "load_provider", return_value=plugin), patch.dict(os.environ, {"DECISION_API_KEY": "fixture"}), self.assertRaises(ProviderError) as error:
            cli.evaluate_request(REQUEST, self.args())
        self.assertNotIn("fixture-key", str(error.exception))

    def test_schema_constraints_cannot_widen_common_request_authority(self):
        from jsonschema import ValidationError, validate

        plugin = provider()
        plugin.schema_constraints.return_value = {"properties": {"provider": {"type": "string"}}, "additionalProperties": True}
        schema = cli.request_schema(plugin)
        validate(REQUEST, schema)
        for field in ("provider", "base_url", "api_key_env", "account_id"):
            with self.assertRaises(ValidationError):
                validate({**REQUEST, field: "override"}, schema)
        # The composed schemas retain bundled constraints and common validation.
        for name in ("openai", "cloudflare", "gemini"):
            schema = cli.request_schema(name)
            validate(REQUEST, schema)
            with self.assertRaises(ValidationError):
                validate({"state": "x", "questions": {"q": {"type": "box", "instructions": "Where?"}}}, schema)


if __name__ == "__main__":
    unittest.main()
