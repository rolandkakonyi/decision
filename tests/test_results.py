"""All providers reject malformed complete batches through the native host."""
import copy
import json
import unittest
from native import evaluate
from provider_fixtures import CLOUDFLARE_RESPONSE, OPENAI_RESPONSE, QUESTIONS


class ResultValidationTests(unittest.TestCase):
    def test_invalid_answers_reject_the_entire_batch(self):
        changes = [lambda r: r['answers']['present'].update(noul=2),
                   lambda r: r['answers']['breed'].update(choice='not-offered'),
                   lambda r: r['answers']['breed'].update(confidence=2),
                   lambda r: r['answers']['breed'].update(probabilities={'other': 1}),
                   lambda r: r['answers']['breed'].update(probabilities={'french': -1, 'english': 2}),
                   lambda r: r['answers']['visible'].update(score=2),
                   lambda r: r['answers']['visible'].update(legend={'0': 'Hidden'}),
                   lambda r: r['answers'].pop('present'),
                   lambda r: r['usage'].update(input_tokens=-1)]
        for change in changes:
            raw = copy.deepcopy(CLOUDFLARE_RESPONSE)
            change(raw['result'])
            result, _ = evaluate('cloudflare', {'state': 'dog', 'questions': QUESTIONS}, raw)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')

    def test_probability_totals_for_both_provider_adapters(self):
        for total in (0, .5, 1.8):
            for provider in ('openai', 'cloudflare'):
                for kind in ('choice', 'score'):
                    with self.subTest(total=total, provider=provider, kind=kind):
                        raw = copy.deepcopy(OPENAI_RESPONSE if provider == 'openai' else CLOUDFLARE_RESPONSE)
                        if provider == 'openai':
                            answer = next(a for a in raw['answers'] if a['type'] == kind)
                            for row in answer['probabilities']:
                                row['probability'] = total / 2
                        else:
                            answer = next(a for a in raw['result']['answers'].values() if a['type'] == kind)
                            answer['probabilities'] = {key: total / 2 for key in answer['probabilities']}
                        result, _ = evaluate(provider, {'state': 'dog', 'questions': QUESTIONS}, raw)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, '')

    def test_rounded_large_distribution_remains_valid(self):
        criteria = {str(i): str(i) for i in range(255)}
        raw = {'success': True, 'errors': [], 'messages': [], 'result': {
            'model': 'clef', 'usage': {}, 'answers': {'pick': {
                'type': 'choice', 'choice': '0', 'confidence': .1,
                'probabilities': {key: round(1 / 255, 4) for key in criteria}}}}}
        result, _ = evaluate('cloudflare', {'state': 'x', 'questions': {'pick': {'type': 'choice', 'instructions': 'Pick', 'criteria': criteria}}}, raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['answers']['pick']['probabilities']), 255)

    def test_box_edges_are_finite_ordered_fractions(self):
        raw = {'model': 'fixture', 'usage': {}, 'answers': {'location': {'type': 'box', 'confidence': None,
               'box': {'left': 0, 'top': 0, 'right': 1, 'bottom': 1}}}}
        payload = {'state': 'x', 'questions': {'location': {'type': 'box', 'instructions': 'Locate'}}}
        result, _ = evaluate('typesafe', payload, raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        for field, value in (('left', 2), ('right', -1), ('top', float('inf'))):
            invalid = copy.deepcopy(raw)
            invalid['answers']['location']['box'][field] = value
            result, _ = evaluate('typesafe', payload, invalid)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
