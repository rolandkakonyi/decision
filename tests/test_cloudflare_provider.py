"""Clef wire mapping, request limits, and strict response envelopes."""
import base64
import copy
import json
import unittest
from native import evaluate, invoke, response_server
from provider_fixtures import CLOUDFLARE_RESPONSE as RESPONSE, QUESTIONS, png_bytes


class CloudflareProviderTests(unittest.TestCase):
    def preview(self, payload, *flags, env=None):
        return invoke('run', '-', '--provider', 'cloudflare', '--dry-run', '--json', *flags,
                      data=json.dumps(payload), env=env)

    def test_defaults_model_and_account_validation(self):
        payload = {'state': 'dog', 'questions': QUESTIONS}
        result = self.preview(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['model'], 'clef')
        result = self.preview(payload, '--model', '@cf/cloudflare/clef-flash')
        self.assertEqual(json.loads(result.stdout)['model'], 'clef-flash')
        for model in ('jev-1.14.0', 'clef-omni', '../../other'):
            self.assertEqual(self.preview(payload, '--model', model).returncode, 2)
        for account in ('', 'not/an/account'):
            result = invoke('run', '-', '--provider', 'cloudflare', '--account-id', account,
                            data=json.dumps(payload), env={'CLOUDFLARE_API_TOKEN': 'fixture'})
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')

    def test_host_scope_and_explicit_selector(self):
        with response_server(RESPONSE) as (url, requests):
            result = invoke('noul', 'Dog?', '--state', 'dog', '--provider', 'cloudflare',
                            '--base-url', url, '--json', env={'CLOUDFLARE_API_TOKEN': 'private-fixture'})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(requests, [])
            self.assertNotIn('private-fixture', result.stderr)

    def test_named_images_and_scalar_context(self):
        images = {'<dog>': png_bytes(), '<other>': b'\xff\xd8\xffjpeg-fixture'}
        payload = {'state': {'images': {'dog': '<dog>', 'other': '<other>'}, 'message': 'Compare'},
                   'questions': QUESTIONS, 'model': '@cf/cloudflare/clef-flash',
                   'assets': {name: {'data_base64': base64.b64encode(data).decode()} for name, data in images.items()}}
        result, requests = evaluate('cloudflare', payload, RESPONSE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(requests), 1)
        body = requests[0]['body']
        self.assertTrue(requests[0]['path'].endswith('/@cf/cloudflare/clef-flash'))
        self.assertEqual(body['model'], 'clef-flash')
        self.assertEqual(body['questions'], QUESTIONS)
        self.assertEqual(body['state']['_decision_image_references'], [
            {'reference': '<dog>', 'image_position_1_based': 1},
            {'reference': '<other>', 'image_position_1_based': 2}])
        for encoded, expected in zip(body['images'], images.values()):
            self.assertEqual(base64.b64decode(encoded.split(',', 1)[1]), expected)
        for state in (False, 0, 'text', [1, 2]):
            result, calls = evaluate('cloudflare', {'state': state, 'questions': QUESTIONS}, RESPONSE)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[0]['body']['state'], state)
        payload['state']['_decision_image_references'] = 'reserved'
        self.assertEqual(self.preview(payload).returncode, 2)

    def test_question_and_media_limits(self):
        bad = [{str(i): QUESTIONS['present'] for i in range(65)}, {'has space': QUESTIONS['present']},
               {'x' * 101: QUESTIONS['present']}, {'newline\n': QUESTIONS['present']},
               {'location': {'type': 'box'}},
               {'breed': {**QUESTIONS['breed'], 'criteria': {'only': None}}},
               {'breed': {**QUESTIONS['breed'], 'criteria': {str(i): None for i in range(256)}}},
               {'visible': {**QUESTIONS['visible'], 'criteria': ['one']}},
               {'visible': {**QUESTIONS['visible'], 'criteria': list(range(11))}}]
        for questions in bad:
            with self.subTest(question=next(iter(questions))):
                questions = {name: {'instructions': 'Question?', **question} for name, question in questions.items()}
                self.assertEqual(self.preview({'state': 'dog', 'questions': questions}).returncode, 2)
        valid = {str(i): QUESTIONS['present'] for i in range(64)}
        self.assertEqual(self.preview({'state': 'dog', 'questions': valid}).returncode, 0)
        for sizes in ([100] * 5, [4 * 1024 * 1024 + 1], [3 * 1024 * 1024] * 3):
            assets = {f'<{i}>': {'data_base64': base64.b64encode(png_bytes() + b'x' * size).decode()}
                      for i, size in enumerate(sizes)}
            payload = {'state': list(assets), 'questions': QUESTIONS, 'assets': assets}
            self.assertEqual(self.preview(payload).returncode, 2)
        self.assertEqual(self.preview({'state': 'x' * (13 * 1024 * 1024), 'questions': QUESTIONS}).returncode, 2)

    def test_usage_and_malformed_envelopes(self):
        for usage in ({}, {'input_tokens': 0, 'output_tokens': None}):
            raw = copy.deepcopy(RESPONSE)
            raw['result']['usage'] = usage
            result, _ = evaluate('cloudflare', {'state': 'dog', 'questions': QUESTIONS}, raw)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['usage'], {'input_tokens': usage.get('input_tokens'), 'output_tokens': None})
        changes = [lambda r: r.update(success=False), lambda r: r.update(success=1),
                   lambda r: r.update(errors=[{'message': 'private context'}]),
                   lambda r: r['result'].update(model=''),
                   lambda r: r['result']['answers'].pop('present'),
                   lambda r: r['result']['answers']['present'].update(type='box'),
                   lambda r: r['result']['answers']['present'].update(noul=2),
                   lambda r: r['result']['answers']['breed'].update(choice='unknown'),
                   lambda r: r['result']['answers']['breed']['probabilities'].pop('english'),
                   lambda r: r['result']['answers']['visible'].update(score=-1),
                   lambda r: r['result']['answers']['visible']['legend'].pop('1'),
                   lambda r: r['result']['usage'].update(input_tokens=-1)]
        for index, change in enumerate(changes):
            raw = copy.deepcopy(RESPONSE)
            change(raw)
            with self.subTest(index=index):
                result, calls = evaluate('cloudflare', {'state': 'dog', 'questions': QUESTIONS}, raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('private context', result.stderr)
                self.assertEqual(len(calls), 1)
        result, _ = evaluate('cloudflare', {'state': 'dog', 'questions': QUESTIONS}, b'{"success":true,"success":false}')
        self.assertNotEqual(result.returncode, 0)
