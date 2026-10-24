import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chatwell import prompts, validation
from chatwell.client import MockClient
from chatwell.history import character_history
from chatwell.session import run
from chatwell.speakers import select_speaker
from session_view import read_json

ROOT = Path(__file__).resolve().parents[1]


class RecordingClient(MockClient):
    def __init__(self):
        self.calls = []

    def complete(self, messages, options, schema=None):
        data = json.loads(messages[-1]['content'])
        if schema == prompts.TURN_SCHEMA:
            data['character'] = json.loads(messages[0]['content'].split('\nCharacter definition (JSON data):\n', 1)[1])
        self.calls.append((copy.deepcopy(data), schema))
        result = super().complete(messages, options, schema)
        if schema == prompts.INITIAL_SCHEMA:
            result['goal'] = 'REFINED_GOAL_SECRET' if data.get('goal') else ''
            result['situation'] = 'REFINED_SCENE'
        elif schema == prompts.TURN_SCHEMA:
            result['thought'] = f"PRIVATE_{data['character']['name']}"
            result['narration'] = 'ORIGINAL_NARRATION'
        elif 'progress' in schema['properties']:
            result['progress_reason'] = 'PRIVATE_ASSESSMENT'
        return result


class MultiTurnTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(ROOT / 'tests/fixtures/examples/config.json')
        self.config['goal'] = 'ORIGINAL_GOAL_SECRET'
        self.characters = [read_json(ROOT / 'tests/fixtures/examples' / f'{n}.json') for n in ('alice', 'bob')]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_three_turns_privacy_and_separate_narrations(self):
        client = RecordingClient()
        snapshots = []
        path = run(self.config, self.characters, client, self.temp.name,
                   lambda p: snapshots.append(read_json(p)))
        result = read_json(path)
        conversation = result['conversation']
        self.assertEqual(result['config'], self.config)
        self.assertEqual(result['characters'], self.characters)
        self.assertEqual(conversation['situation'], 'REFINED_SCENE')
        self.assertEqual(conversation['goal'], 'REFINED_GOAL_SECRET')
        self.assertEqual(conversation['non_participants'], ['Charlie'])
        self.assertEqual(conversation['stop_reason'], 'goal_achieved')
        self.assertEqual([t['character']['name'] for t in conversation['turns']], ['Alice', 'Bob', 'Alice'])
        self.assertEqual(len(client.calls), 8)  # init + three character/update pairs + closing
        self.assertTrue(conversation['ending_narration'])
        character_calls = [d for d, s in client.calls if s == prompts.TURN_SCHEMA]
        for index, data in enumerate(character_calls):
            encoded = json.dumps(data)
            for private in ('ORIGINAL_GOAL_SECRET', 'REFINED_GOAL_SECRET', 'PRIVATE_ASSESSMENT',
                            'progress_reason', 'speaker_recommendation', 'ORIGINAL_NARRATION'):
                self.assertNotIn(private, encoded)
            self.assertNotIn('goal', data)
            self.assertEqual(len(data['history']), index)
            self.assertEqual(data['situation'], 'REFINED_SCENE')
            for participant in data['participants']:
                self.assertNotIn('persona', participant)
            for historic in data['history']:
                if historic['name'] == data['character']['name']:
                    self.assertEqual(historic['thought'], f"PRIVATE_{historic['name']}")
                else:
                    self.assertNotIn('thought', historic)
                self.assertEqual(historic['narration'], '静かな店内で、会話が続いた。')
        self.assertEqual(character_calls[1]['current_situation'], conversation['turns'][0]['current_situation'])
        updates = [d for d, s in client.calls if 'progress' in s['properties']]
        self.assertTrue(all(d['goal'] == 'REFINED_GOAL_SECRET' for d in updates))
        self.assertEqual(updates[1]['history'][0]['character']['thought'], 'PRIVATE_Alice')
        self.assertTrue(any('pending_turn' in s['conversation'] for s in snapshots))
        self.assertTrue(any('pending_initialization' in s['conversation'] for s in snapshots))
        self.assertNotIn('pending_turn', conversation)
        for turn in conversation['turns']:
            self.assertEqual(turn['character']['narration'], 'ORIGINAL_NARRATION')
            self.assertNotEqual(turn['character']['narration'], turn['narration'])
            self.assertEqual(set(turn['character']), {'name', 'speech', 'thought', 'narration', 'expression'})

    def test_speaker_excludes_previous_and_uses_weighted_fallback(self):
        a, b = self.characters
        turns = [{'character': {'name': 'Alice'}}]
        self.assertIs(select_speaker(self.characters, turns, {'name': 'Alice', 'confidence': 1}), b)
        for recommendation in ({'name': 'Charlie', 'confidence': 1}, {}, None,
                               {'confidence': 0.99}, {'name': 'Alice'}, {'name': 'Bob', 'confidence': True}):
            self.assertIs(select_speaker(self.characters, turns, recommendation), b)
        self.assertIs(select_speaker([a], turns, {}), a)
        c = {**a, 'name': 'Charlie'}
        turns = [{'character': {'name': name}} for name in ('Alice', 'Bob', 'Bob', 'Bob', 'Alice')]
        with patch('chatwell.speakers.random.choices', return_value=[c]) as choose:
            self.assertIs(select_speaker([a, b, c], turns, {'name': 'Bob', 'confidence': .74}), c)
            choose.assert_called_once_with([b, c], weights=[.25, 1.0], k=1)
        with patch('chatwell.speakers.random.choices') as choose:
            self.assertIs(select_speaker([a, b, c], turns, {'name': 'Bob', 'confidence': .75}), b)
            choose.assert_not_called()

    def test_character_system_and_public_recommendation_context(self):
        calls = []
        client = MockClient()
        original = client.complete
        def capture(messages, options, schema):
            calls.append((copy.deepcopy(messages), schema))
            return original(messages, options, schema)
        client.complete = capture
        run(self.config, self.characters, client, self.temp.name, max_turns=2)
        acting = [(m, schema) for m, schema in calls if schema == prompts.TURN_SCHEMA]
        for index, (messages, _) in enumerate(acting):
            data = json.loads(messages[-1]['content'])
            self.assertNotIn('character', data)
            self.assertIn(json.dumps({k: v for k, v in self.characters[index].items() if k != 'profile'}, ensure_ascii=False), messages[0]['content'])
            self.assertEqual(len(data['history']), index)
            self.assertNotIn('ORIGINAL_GOAL_SECRET', messages[0]['content'])
            self.assertNotIn(self.characters[1-index]['persona'], messages[0]['content'])
            self.assertNotIn(self.characters[index]['profile'], json.dumps(messages, ensure_ascii=False))
            self.assertEqual(next(p for p in data['participants'] if p['name'] == self.characters[index]['name']),
                             {'name': self.characters[index]['name']})
            self.assertEqual(next(p for p in data['participants'] if p['name'] == self.characters[1-index]['name'])['profile'],
                             self.characters[1-index]['profile'])
        updates = [(json.loads(m[-1]['content']), schema) for m, schema in calls if 'progress' in schema['properties']]
        for index, (data, schema) in enumerate(updates):
            context = data['speaker_context']
            previous = self.characters[index]['name']
            self.assertNotIn(previous, context['candidates'])
            self.assertEqual(schema['properties']['speaker_recommendation']['properties']['name']['enum'], context['candidates'])
            self.assertEqual(context['completed_message']['name'], previous)
            for message in context['preceding_messages'] + [context['completed_message']]:
                self.assertNotIn('thought', message)
                self.assertNotIn('persona', message)

    def test_achieved_does_not_select_or_generate_again(self):
        original = MockClient.complete

        def achieved(client, messages, options, schema):
            result = original(client, messages, options, schema)
            if 'progress' in schema['properties']:
                result['progress'] = 'Achieved'
            return result

        with patch.object(MockClient, 'complete', achieved), patch('chatwell.session.select_speaker') as select:
            path = run(self.config, self.characters, MockClient(), self.temp.name)
        select.assert_not_called()
        self.assertEqual(len(read_json(path)['conversation']['turns']), 1)

    def test_turn_limit_and_no_goal(self):
        for goal in (True, False):
            config = copy.deepcopy(self.config)
            if not goal:
                config.pop('goal')
            path = run(config, self.characters, MockClient(), self.temp.name, max_turns=2)
            result = read_json(path)
            self.assertEqual(result['conversation']['stop_reason'], 'max_turns_reached')
            self.assertEqual(len(result['conversation']['turns']), 2)
            if not goal:
                self.assertEqual(result['conversation']['goal'], '')
                self.assertTrue(all(t['progress'] == 'Unchanged' for t in result['conversation']['turns']))

    def test_no_goal_ignores_spurious_achieved(self):
        config = copy.deepcopy(self.config)
        config.pop('goal')
        original = MockClient.complete

        def achieved(client, messages, options, schema):
            result = original(client, messages, options, schema)
            if 'progress' in schema['properties']:
                result['progress'] = 'Achieved'
            return result

        with patch.object(MockClient, 'complete', achieved):
            path = run(config, self.characters, MockClient(), self.temp.name, max_turns=3)
        self.assertEqual(read_json(path)['conversation']['stop_reason'], 'max_turns_reached')
        self.assertEqual(len(read_json(path)['conversation']['turns']), 3)

    def test_pending_character_survives_update_failure(self):
        original = MockClient.complete

        def fail(client, messages, options, schema):
            if 'progress' in schema['properties']:
                raise TimeoutError('update timed out')
            return original(client, messages, options, schema)

        with patch.object(MockClient, 'complete', fail):
            with self.assertRaises(TimeoutError):
                run(self.config, self.characters, MockClient(), self.temp.name)
        result = read_json(next(Path(self.temp.name).glob('*.json')))
        self.assertEqual(result['status'], 'Failed')
        self.assertIsNotNone(result['timestamps']['ended'])
        self.assertEqual(result['conversation']['turns'], [])
        self.assertEqual(result['conversation']['pending_turn']['character']['speech'], '今日は静かですね。')

    def test_invalid_update_retains_received_output(self):
        original = MockClient.complete

        def fail(client, messages, options, schema):
            result = original(client, messages, options, schema)
            if 'progress' in schema['properties']:
                result['progress'] = 'Wrong'
            return result

        with patch.object(MockClient, 'complete', fail):
            with self.assertRaises(ValueError):
                run(self.config, self.characters, MockClient(), self.temp.name)
        result = read_json(next(Path(self.temp.name).glob('*.json')))
        self.assertEqual(result['status'], 'Failed')
        self.assertEqual(result['conversation']['pending_turn']['coordinator']['progress'], 'Wrong')

    def test_missing_or_invalid_recommendation_falls_back(self):
        original = MockClient.complete
        for recommendation in (None, {}, {'name': 'Charlie', 'confidence': 1}, {'confidence': 1}):
            def revise(client, messages, options, schema):
                result = original(client, messages, options, schema)
                if 'progress' in schema['properties']:
                    if recommendation is None:
                        result.pop('speaker_recommendation')
                    else:
                        result['speaker_recommendation'] = recommendation.copy()
                return result
            with self.subTest(recommendation=recommendation), patch.object(MockClient, 'complete', revise):
                path = run(self.config, self.characters, MockClient(), self.temp.name, max_turns=2)
            turns = read_json(path)['conversation']['turns']
            self.assertEqual([t['character']['name'] for t in turns], ['Alice', 'Bob'])
            self.assertIn('speaker_recommendation', turns[0])

    def test_required_fields_and_numeric_bounds(self):
        empty = {field: '' for field in prompts.TURN_SCHEMA['required']}
        validation.turn(empty)
        for field in empty:
            bad = empty.copy()
            bad.pop(field)
            with self.assertRaises(ValueError):
                validation.turn(bad)
        for confidence in (-0.01, 1.01, True, float('nan'), '0.8'):
            with self.assertRaises(ValueError):
                validation.update({'narration': '', 'current_situation': 'Scene', 'progress': 'Unchanged',
                                   'progress_reason': 'Reason', 'speaker_recommendation': {'name': 'Alice', 'confidence': confidence}})
        schema = prompts.update_schema(['Alice', 'Bob'])
        self.assertEqual(schema['properties']['speaker_recommendation']['properties']['name']['enum'], ['Alice', 'Bob'])
        self.assertEqual(schema['properties']['progress']['enum'], list(prompts.PROGRESS))

    def test_invalid_runtime_limits(self):
        for options in ({'max_turns': 0}, {'max_turns': True}, {'recommendation_threshold': 1.1},
                        {'recommendation_threshold': float('nan')}):
            with self.assertRaises(ValueError):
                run(self.config, self.characters, MockClient(), self.temp.name, **options)

    def test_later_update_failure_keeps_completed_history_and_pending(self):
        original = MockClient.complete

        def fail(client, messages, options, schema):
            data = json.loads(messages[-1]['content'])
            if 'progress' in schema['properties'] and len(data['history']) == 1:
                raise KeyboardInterrupt()
            return original(client, messages, options, schema)

        with patch.object(MockClient, 'complete', fail):
            with self.assertRaises(KeyboardInterrupt):
                run(self.config, self.characters, MockClient(), self.temp.name)
        result = read_json(next(Path(self.temp.name).glob('*.json')))
        self.assertEqual(result['status'], 'Failed')
        self.assertEqual(result['conversation']['stop_reason'], 'user_interrupted')
        self.assertEqual(len(result['conversation']['turns']), 1)
        self.assertEqual(result['conversation']['pending_turn']['name'], 'Bob')
        self.assertIsNotNone(result['timestamps']['ended'])

    def test_all_empty_fields_are_saved_as_complete_turn(self):
        original = MockClient.complete

        def empty(client, messages, options, schema):
            if schema == prompts.TURN_SCHEMA:
                return {key: '' for key in schema['required']}
            return original(client, messages, options, schema)

        with patch.object(MockClient, 'complete', empty):
            path = run(self.config, self.characters, MockClient(), self.temp.name, max_turns=1)
        result = read_json(path)
        self.assertEqual(result['status'], 'Ended')
        self.assertEqual(result['conversation']['turns'][0]['character'],
                         {'name': 'Alice', 'speech': '', 'thought': '', 'narration': '', 'expression': ''})


if __name__ == '__main__':
    unittest.main()
