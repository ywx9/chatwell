import copy
import json
from pathlib import Path
import unittest

from chatwell import prompts
from chatwell.history import character_input
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class CharacterPromptTests(unittest.TestCase):
    def setUp(self):
        self.characters = [read_json(ROOT / 'tests/fixtures/examples' / f'{name}.json')
                           for name in ('alice', 'bob')]
        self.alice, self.bob = self.characters
        self.conversation = {
            'beginning_narration': 'PUBLIC_OPENING',
            'non_participants': ['Charlie'],
            'turns': [],
        }

    def add_turn(self, character):
        self.conversation['turns'].append({
            'character': {'name': character['name'], 'speech': 'PUBLIC_SPEECH',
                          'thought': 'SECRET_' + character['name'],
                          'narration': 'DRAFT_NARRATION', 'expression': 'PUBLIC_EXPRESSION'},
            'narration': 'DEFINITIVE_NARRATION',
        })

    def build(self, character):
        original = copy.deepcopy(self.conversation)
        data = character_input(self.conversation, character, self.characters, 'Japanese')
        messages = prompts.messages('character', data)
        self.assertEqual(self.conversation, original)
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        definition = json.loads(messages[0]['content'].split('\nCharacter definition (JSON data):\n')[1])
        self.assertEqual(definition, {k: v for k, v in character.items() if k != 'profile'})
        self.assertNotIn('character', json.loads(messages[1]['content']))
        return data, messages[0]['content']

    def assert_phase(self, instructions, phase):
        # Exactly one scene guide is appended, before the acting definition.
        for candidate in ('initial', 'continuing', 'compressed'):
            guide = (prompts.ROOT / f'character_{candidate}.txt').read_text(encoding='utf-8')
            if candidate == phase:
                self.assertIn(guide, instructions)
                self.assertLess(instructions.index(guide), instructions.index('Character definition (JSON data):'))
            else:
                self.assertNotIn(guide, instructions)

    def test_first_conversation_turn_has_only_opening_and_initial_guide(self):
        data, instructions = self.build(self.alice)
        self.assert_phase(instructions, 'initial')
        self.assertEqual(data['beginning_narration'], 'PUBLIC_OPENING')
        self.assertEqual(data['history'], [])
        self.assertNotIn('memory', data)
        self.assertNotIn('private_memory', data)

    def test_another_characters_first_turn_continues_existing_conversation(self):
        self.add_turn(self.alice)
        data, instructions = self.build(self.bob)
        self.assert_phase(instructions, 'continuing')
        self.assertEqual(data['beginning_narration'], 'PUBLIC_OPENING')
        self.assertEqual(len(data['history']), 1)
        self.assertNotIn('thought', data['history'][0])
        self.assertEqual(data['history'][0]['narration'], 'DEFINITIVE_NARRATION')
        self.assertNotIn('memory', data)
        self.assertNotIn('private_memory', data)

    def test_compressed_context_preserves_public_and_private_boundaries(self):
        self.add_turn(self.alice)
        self.add_turn(self.bob)
        self.add_turn(self.alice)
        self.conversation['compression'] = {
            'through_turn': 0, 'public_facts': ['PUBLIC_SUMMARY'],
            'private_thoughts': {'Alice': ['EARLIER_ALICE_SECRET'], 'Bob': ['EARLIER_BOB_SECRET']},
        }
        data, instructions = self.build(self.alice)
        self.assert_phase(instructions, 'compressed')
        self.assertNotIn('beginning_narration', data)
        self.assertEqual(data['memory'], {'through_turn': 0, 'facts': ['PUBLIC_SUMMARY']})
        self.assertEqual(data['private_memory'], ['EARLIER_ALICE_SECRET'])
        self.assertEqual([t['name'] for t in data['history']], ['Bob', 'Alice'])
        self.assertNotIn('thought', data['history'][0])
        self.assertEqual(data['history'][1]['thought'], 'SECRET_Alice')
        self.assertNotIn('SECRET_Bob', json.dumps(data))
        self.assertNotIn('EARLIER_BOB_SECRET', json.dumps(data))
        data['memory']['facts'].append('LOCAL_CHANGE')
        data['private_memory'].append('LOCAL_CHANGE')
        self.assertEqual(self.conversation['compression']['public_facts'], ['PUBLIC_SUMMARY'])
        self.assertEqual(self.conversation['compression']['private_thoughts']['Alice'], ['EARLIER_ALICE_SECRET'])

    def test_empty_uncompressed_history_still_uses_compressed_guide(self):
        self.add_turn(self.alice)
        self.conversation['compression'] = {
            'through_turn': 0, 'public_facts': ['PUBLIC_SUMMARY'], 'private_thoughts': {},
        }
        data, instructions = self.build(self.bob)
        self.assert_phase(instructions, 'compressed')
        self.assertEqual(data['history'], [])
        self.assertEqual(data['private_memory'], [])
        self.assertNotIn('beginning_narration', data)
