import copy
import json
from pathlib import Path
import tempfile
import unittest

from chatwell import prompts, validation
from chatwell.client import MockClient
from chatwell.session import run
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class CapturedClient(MockClient):
    def __init__(self):
        self.calls = []

    def complete(self, messages, options, schema=None):
        self.calls.append((copy.deepcopy(messages), schema))
        return super().complete(messages, options, schema)


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(ROOT / 'tests/fixtures/examples/config.json')
        self.characters = [read_json(ROOT / 'tests/fixtures/examples' / f'{name}.json') for name in ('alice', 'bob')]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def generate(self, goal, client=None, turns=5):
        self.config['goal'] = goal
        return read_json(run(self.config, self.characters, client or MockClient(), self.temp.name, max_turns=turns))

    def test_personal_goals_are_visible_only_to_the_owner_and_evaluator(self):
        client = CapturedClient()
        goals = {'Alice': 'PRIVATE_ALICE_GOAL', 'Bob': 'PRIVATE_BOB_GOAL'}
        result = self.generate(goals, client)
        self.assertEqual(result['status'], 'Ended')
        self.assertEqual(result['conversation']['initialization']['output']['goal'], goals)
        initial = json.loads(client.calls[0][0][-1]['content'])
        self.assertIsNone(initial['goal'])
        self.assertEqual(set(initial['characters'][0]), {'name', 'profile'})
        self.assertIn('mentioned_non_participants', initial)
        for messages, schema in client.calls:
            data = json.loads(messages[-1]['content'])
            if schema == prompts.TURN_SCHEMA:
                actor = json.loads(messages[0]['content'].split('\nCharacter definition (JSON data):\n', 1)[1])
                self.assertEqual(actor['goal'], goals[actor['name']])
                self.assertNotIn('situation', data)
                self.assertNotIn('current_situation', data)
                for name, goal in goals.items():
                    if name != actor['name']:
                        self.assertNotIn(goal, json.dumps(messages))
            elif 'progress' in schema['properties']:
                self.assertEqual(data['goal'], goals)
                self.assertEqual(set(schema['properties']['progress']['properties']), set(goals))
                self.assertNotIn('private_memory', data)
                self.assertNotIn('thought', data['character'])
                self.assertTrue(all(set(person) == {'name', 'profile'} for person in data['characters']))
                self.assertTrue(all(set(turn) == {'name', 'speech', 'narration', 'expression'} for turn in data['history']))

    def test_stop_requires_all_assigned_goals_to_be_achieved(self):
        class Staggered(CapturedClient):
            def complete(self, messages, options, schema=None):
                result = super().complete(messages, options, schema)
                if 'progress' in schema['properties']:
                    number = json.loads(messages[-1]['content'])['completed_turns'] + 1
                    result['progress'] = {'Alice': 'Achieved' if number >= 2 else 'Approaching',
                                          'Bob': 'Achieved' if number >= 4 else 'Approaching'}
                return result
        result = self.generate({'Alice': 'A', 'Bob': 'B'}, Staggered(), turns=6)
        self.assertEqual(result['conversation']['stop_reason'], 'goal_achieved')
        self.assertEqual(len(result['conversation']['turns']), 4)

    def test_partial_and_empty_goal_objects(self):
        client = CapturedClient()
        result = self.generate({'Bob': 'BOB_ONLY_GOAL'}, client)
        self.assertEqual(result['conversation']['stop_reason'], 'goal_achieved')
        for messages, schema in client.calls:
            if schema == prompts.TURN_SCHEMA:
                actor = json.loads(messages[0]['content'].split('\nCharacter definition (JSON data):\n', 1)[1])
                if actor['name'] == 'Alice':
                    self.assertNotIn('goal', actor)
            elif 'progress' in schema['properties']:
                self.assertEqual(list(schema['properties']['progress']['properties']), ['Bob'])
        result = self.generate({}, turns=2)
        self.assertEqual(result['conversation']['stop_reason'], 'max_turns_reached')
        self.assertEqual(len(result['conversation']['turns']), 2)
        self.assertTrue(all(turn['coordinator']['output']['progress'] == {} for turn in result['conversation']['turns']))

    def test_invalid_goals_and_mismatched_progress_are_rejected(self):
        for goal in ({'Charlie': 'unknown'}, {'Alice': ''}, [], True, 3):
            self.config['goal'] = goal
            with self.subTest(goal=goal), self.assertRaises(ValueError):
                validation.request(self.config, self.characters)
        base = {'narration': '', 'current_situation': 'Cafe', 'progress': {'Bob': 'Achieved'},
                'progress_reason': {'Bob': 'Completed'}}
        with self.assertRaises(ValueError):
            validation.update(base, {'Alice': 'A'})
        with self.assertRaises(ValueError):
            validation.update(base, 'common goal')
        with self.assertRaises(ValueError):
            validation.update({**base, 'progress': 'Achieved', 'progress_reason': 'Completed'}, {'Bob': 'B'})

    def test_common_goal_keeps_single_status_and_is_not_sent_to_actors(self):
        client = CapturedClient()
        result = self.generate('PRIVATE_COMMON_GOAL', client)
        self.assertEqual(result['conversation']['stop_reason'], 'goal_achieved')
        self.assertIsInstance(result['conversation']['turns'][-1]['coordinator']['output']['progress'], str)
        for messages, schema in client.calls:
            if schema == prompts.TURN_SCHEMA:
                self.assertNotIn('PRIVATE_COMMON_GOAL', json.dumps(messages))
