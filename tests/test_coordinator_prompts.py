import copy
import json
from pathlib import Path
import tempfile
import unittest

from chatwell import goals, prompts
from chatwell.client import MockClient
from chatwell.session import run
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class CapturedCoordinatorClient(MockClient):
    context_size = 100
    default_max_tokens = 20

    def __init__(self):
        self.calls = []
        self.updates = []
        self.turn = -1

    def complete(self, messages, options, schema=None):
        self.calls.append(copy.deepcopy(messages))
        if schema == prompts.TURN_SCHEMA:
            self.turn += 1
        if 'progress' in schema['properties']:
            self.updates.append((copy.deepcopy(messages), copy.deepcopy(schema)))
        result = super().complete(messages, options, schema)
        if schema == prompts.TURN_SCHEMA:
            result.update(speech=f'SPEECH_{self.turn}', thought=f'PRIVATE_THOUGHT_{self.turn}',
                          narration=f'DRAFT_ACTION_{self.turn}')
        elif 'progress' in schema['properties']:
            result.update(narration=f'CONFIRMED_ACTION_{self.turn}',
                          current_situation=f'STATE_AFTER_{self.turn}')
        # Trigger compression after turn 1, so turn 2 exercises compressed input.
        self.last_metrics = {'prompt_tokens': 81, 'completion_tokens': 5, 'finish_reason': 'stop'}
        return result


class CoordinatorPromptTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(ROOT / 'tests/fixtures/examples/config.json')
        self.config['llm_config'] = {'max_tokens': 20}
        self.characters = [read_json(ROOT / 'tests/fixtures/examples' / f'{name}.json')
                           for name in ('alice', 'bob')]

    def generate(self, goal):
        config = copy.deepcopy(self.config)
        config.pop('goal', None)
        if goal is not None:
            config['goal'] = goal
        client = CapturedCoordinatorClient()
        with tempfile.TemporaryDirectory() as directory:
            result = read_json(run(config, self.characters, client, directory, max_turns=3))
        self.assertEqual(result['status'], 'Ended')
        self.assertEqual(len(client.updates), 3)
        return client, result

    def assert_guides(self, system, history_mode, has_goal):
        guides = {
            'update_uncompressed': history_mode == 'uncompressed',
            'update_compressed': history_mode == 'compressed',
            'update_evaluation': has_goal,
            'update_no_goals': not has_goal,
        }
        common = (prompts.ROOT / 'update.txt').read_text(encoding='utf-8')
        self.assertTrue(system.startswith(common))
        for name, expected in guides.items():
            guide = (prompts.ROOT / f'{name}.txt').read_text(encoding='utf-8')
            if expected:
                self.assertIn(guide, system)
            else:
                self.assertNotIn(guide, system)

    def test_only_applicable_goal_and_history_guides_reach_each_update(self):
        cases = ['COMMON_GOAL', {'Alice': 'ALICE_GOAL'}, None, {}]
        for goal in cases:
            with self.subTest(goal=goal):
                client, result = self.generate(goal)
                for index, (messages, schema) in enumerate(client.updates):
                    self.assertEqual([m['role'] for m in messages], ['system', 'user'])
                    data = json.loads(messages[1]['content'])
                    self.assert_guides(messages[0]['content'],
                                       'compressed' if index == 2 else 'uncompressed',
                                       bool(goal))
                    self.assertNotIn('goal', data)
                    self.assertEqual(data['goals'], goals.normalize(goal))
                    for key in ('progress', 'progress_reason'):
                        self.assertEqual(schema['properties'][key]['type'], 'object')
                        self.assertEqual(set(schema['properties'][key]['properties']), set(data['goals']))
                    self.assertIn('speaker_context', data)
                    self.assertIn('speaker_recommendation', schema['required'])
                # Two calls per turn plus initialization and ending; compression adds its own calls.
                compression_count = len(result['conversation'].get('compressions', []))
                self.assertEqual(len(client.calls) - compression_count, 8)

    def test_update_input_separates_previous_state_and_current_turn(self):
        client, result = self.generate(None)
        initial = result['conversation']['initialization']['output']
        for index, (messages, _) in enumerate(client.updates):
            data = json.loads(messages[1]['content'])
            self.assertEqual(data['completed_turns'], index)
            self.assertEqual(data['current_situation'],
                             initial['situation'] if index == 0 else f'STATE_AFTER_{index - 1}')
            self.assertEqual(data['character']['speech'], f'SPEECH_{index}')
            self.assertEqual(data['character']['narration'], f'DRAFT_ACTION_{index}')
            self.assertNotIn('thought', data['character'])
            self.assertNotIn('PRIVATE_THOUGHT_', json.dumps(data))
            self.assertNotIn(f'SPEECH_{index}', [t['speech'] for t in data['history']])
            expected_turns = range(1 if index == 2 else 0, index)
            self.assertEqual([t['speech'] for t in data['history']],
                             [f'SPEECH_{i}' for i in expected_turns])
            self.assertEqual([t['narration'] for t in data['history']],
                             [f'CONFIRMED_ACTION_{i}' for i in expected_turns])
            if index == 2:
                self.assertEqual(data['memory']['through_turn'], 0)
                self.assertNotIn('beginning_narration', data)
            else:
                self.assertEqual(data['beginning_narration'], initial['beginning_narration'])
                self.assertNotIn('memory', data)

    def test_empty_remaining_history_is_still_an_update_after_current_turn(self):
        client, _ = self.generate(None)
        data = json.loads(client.updates[-1][0][1]['content'])
        data['history'] = []
        original = copy.deepcopy(data)
        messages = prompts.messages('update', data)
        self.assert_guides(messages[0]['content'], 'compressed', False)
        self.assertEqual(data, original)
        self.assertEqual(json.loads(messages[1]['content']), original)

    def test_common_and_individual_goals_use_identical_evaluation_instructions(self):
        common, _ = self.generate('Submit the report and obtain approval.')
        personal, _ = self.generate({'Alice': 'Submit the report and obtain approval.'})
        for (common_messages, _), (personal_messages, _) in zip(common.updates, personal.updates):
            self.assertEqual(common_messages[0]['content'], personal_messages[0]['content'])
