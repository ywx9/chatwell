import copy
import json
from pathlib import Path
import tempfile
import unittest

from chatwell import prompts
from chatwell.client import MockClient
from chatwell.compression import FACTS_SCHEMA
from chatwell.session import run
from chatwell.settings import inference_settings, read_settings
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class MeasuredClient(MockClient):
    context_size = 100
    default_max_tokens = 20

    def __init__(self, pressures=None, baseline=60):
        self.pressures = pressures or {}
        self.baseline = baseline
        self.acting = []
        self.updates = []
        self.summaries = []
        self.endings = []
        self.current = -1

    def complete(self, messages, options, schema=None):
        data = json.loads(messages[-1]['content'])
        stage = 'other'
        if schema == prompts.TURN_SCHEMA:
            stage = 'character'
            self.current += 1
            self.acting.append(copy.deepcopy(messages))
        elif 'progress' in schema['properties']:
            stage = 'coordinator'
            self.updates.append(copy.deepcopy(data))
        elif schema == FACTS_SCHEMA:
            self.summaries.append(copy.deepcopy(data))
        elif schema == prompts.ENDING_SCHEMA:
            self.endings.append(copy.deepcopy(data))
        result = super().complete(messages, options, schema)
        if schema == prompts.TURN_SCHEMA:
            definition = json.loads(messages[0]['content'].split('\nCharacter definition (JSON data):\n', 1)[1])
            result['thought'] = 'SECRET_' + definition['name']
            result['speech'] = f'PUBLIC_TURN_{self.current}'
        self.last_metrics = {'prompt_tokens': self.pressures.get((stage, self.current), self.baseline),
                             'completion_tokens': 5, 'finish_reason': 'stop'}
        return result


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.data = read_json(ROOT / 'samples/input.json')
        self.data['config'].pop('goal', None)
        self.data['config']['llm_config'] = {'max_tokens': 20}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def generate(self, client, turns):
        return read_json(run(self.data['config'], self.data['characters'], client,
                             self.temp.name, max_turns=turns))

    def test_repeated_compression_merges_oldest_half_and_preserves_originals(self):
        client = MeasuredClient({('character', 5): 81, ('coordinator', 7): 90})
        result = self.generate(client, 8)
        self.assertEqual(result['status'], 'Ended')
        self.assertEqual(result['runtime']['context_size'], 100)
        conversation = result['conversation']
        self.assertEqual(len(conversation['turns']), 8)
        self.assertEqual(conversation['turns'][0]['character']['output']['speech'], 'PUBLIC_TURN_0')
        public_calls = [call for call in conversation['compressions'] if call['scope'] == 'public']
        self.assertEqual([(c['from_turn'], c['through_turn']) for c in public_calls], [(0, 2), (3, 4)])
        self.assertEqual([c['trigger_prompt_tokens'] for c in public_calls], [81, 90])
        self.assertEqual(conversation['compression']['through_turn'], 4)
        public_inputs = [data for data in client.summaries if 'turns' in data]
        self.assertIn('initial_scene', public_inputs[0])
        self.assertIn('beginning_narration', public_inputs[0]['initial_scene'])
        self.assertNotIn('initial_scene', public_inputs[1])
        self.assertEqual(public_inputs[1]['previous_facts'], public_calls[0]['output']['facts'])
        self.assertEqual([t['index'] for t in public_inputs[0]['turns']], [0, 1, 2])
        self.assertEqual([t['index'] for t in public_inputs[1]['turns']], [3, 4])
        sixth = json.loads(client.acting[6][-1]['content'])
        self.assertEqual(sixth['memory']['through_turn'], 2)
        self.assertEqual(len(sixth['history']), 3)
        self.assertNotIn('situation', sixth)
        self.assertNotIn('beginning_narration', sixth)
        self.assertEqual(client.updates[6]['completed_turns'], 6)
        ending = client.endings[0]
        self.assertEqual(ending['memory']['through_turn'], 4)
        self.assertEqual(len(ending['history']), 3)
        self.assertNotIn('private_memory', ending)
        self.assertNotIn('situation', ending)

    def test_private_memories_are_isolated_from_public_and_other_characters(self):
        client = MeasuredClient({('coordinator', 3): 81, ('coordinator', 5): 81})
        result = self.generate(client, 7)
        memory = result['conversation']['compression']
        for source in client.summaries:
            if 'turns' in source:
                self.assertNotIn('SECRET_', json.dumps(source))
                self.assertNotIn('thought', json.dumps(source))
            else:
                for other in self.data['characters']:
                    if other['name'] != source['name']:
                        self.assertNotIn('SECRET_' + other['name'], json.dumps(source, ensure_ascii=False))
        self.assertNotIn('SECRET_', json.dumps(memory['public_facts']))
        for messages in client.acting:
            data = json.loads(messages[-1]['content'])
            if 'memory' not in data:
                continue
            definition = json.loads(messages[0]['content'].split('\nCharacter definition (JSON data):\n', 1)[1])
            for other in self.data['characters']:
                if other['name'] != definition['name']:
                    self.assertNotIn('SECRET_' + other['name'], json.dumps(data, ensure_ascii=False))
        for name, facts in memory['private_thoughts'].items():
            self.assertIn('SECRET_' + name, json.dumps(facts, ensure_ascii=False))
        self.assertNotIn('SECRET_', json.dumps(client.endings, ensure_ascii=False))

    def test_strict_threshold_no_preflight_and_no_minimum_retention(self):
        client = MeasuredClient(baseline=80)  # Exactly context - max_tokens: no compression.
        self.generate(client, 3)
        self.assertEqual(client.summaries, [])
        client = MeasuredClient(baseline=81)
        result = self.generate(client, 3)
        public = [c for c in result['conversation']['compressions'] if c['scope'] == 'public']
        self.assertEqual([c['through_turn'] for c in public], [0, 1])
        # Turn 0 cannot yield an older half yet; subsequent turns compress once each.
        self.assertEqual(len(client.acting), 3)
        self.assertEqual(len(client.updates), 3)

    def test_missing_usage_does_not_trigger_compression(self):
        result = self.generate(MockClient(), 3)
        self.assertNotIn('compression', result['conversation'])
        self.assertNotIn('compressions', result['conversation'])

    def test_failed_summary_retains_last_valid_boundary_and_completed_turns(self):
        class InvalidSummary(MeasuredClient):
            def complete(self, messages, options, schema=None):
                output = super().complete(messages, options, schema)
                data = json.loads(messages[-1]['content'])
                if schema == FACTS_SCHEMA and 'turns' in data and self.current == 2:
                    return {'facts': [None]}
                return output
        client = InvalidSummary(baseline=81)
        with self.assertRaises(ValueError):
            self.generate(client, 4)
        result = read_json(next(Path(self.temp.name).glob('*.json')))
        self.assertEqual(result['status'], 'Failed')
        self.assertEqual(len(result['conversation']['turns']), 3)
        self.assertEqual(result['conversation']['compression']['through_turn'], 0)
        self.assertEqual(result['conversation']['compressions'][-1]['output'], {'facts': [None]})

    def test_interrupt_during_summary_preserves_completed_turns(self):
        class Interrupted(MeasuredClient):
            def complete(self, messages, options, schema=None):
                if schema == FACTS_SCHEMA:
                    raise KeyboardInterrupt
                return super().complete(messages, options, schema)
        with self.assertRaises(KeyboardInterrupt):
            self.generate(Interrupted(baseline=81), 4)
        result = read_json(next(Path(self.temp.name).glob('*.json')))
        self.assertEqual(result['status'], 'Failed')
        self.assertEqual(len(result['conversation']['turns']), 2)
        self.assertNotIn('compression', result['conversation'])
        self.assertEqual(result['conversation']['compressions'][0]['error'], 'interrupted')

    def test_context_setting_and_invalid_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / '.env'
            settings.write_text('LLM_CONTEXT_SIZE=32768\n', encoding='utf-8')
            self.assertEqual(inference_settings(read_settings(settings))['context_size'], 32768)
        self.data['config']['llm_config']['max_tokens'] = 100
        with self.assertRaisesRegex(ValueError, 'context size'):
            self.generate(MeasuredClient(), 2)
