"""Provider selection is explicit and disabled providers never execute."""
import json
import tempfile
import unittest
from pathlib import Path
from native import invoke


class ProviderSelectionTests(unittest.TestCase):
    def test_unregistered_and_disabled_providers(self):
        with tempfile.TemporaryDirectory() as temporary:
            for extra in ([], ['--dry-run'], ['--disable-provider', 'fixture']):
                result = invoke('noul', 'True?', '--state', 'x', '--provider', 'fixture',
                                '--plugin-home', temporary, *extra)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
            for provider in ('typesafe', 'openai', 'cloudflare', 'gemini'):
                result = invoke('noul', 'True?', '--state', 'x', '--provider', provider,
                                '--disable-provider', provider, '--dry-run')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')

    def test_help_and_exports_ignore_ambient_provider_settings(self):
        settings = {'DECISION_PROVIDER': 'unregistered', 'DECISION_BASE_URL': 'invalid',
                    'DECISION_API_KEY_ENV': 'invalid-name', 'DECISION_MODEL': 'invalid'}
        for args in (['--help'], ['--version'], ['noul', '--help'], ['skill']):
            result = invoke(*args, env=settings)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout)
