import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chatwell import prompts, transcript
from chatwell.client import MockClient
from chatwell.session import run
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = read_json(ROOT / 'samples/input.json')

    def generate(self, client=None, **kwargs):
        return run(self.data['config'], self.data['characters'], client or MockClient(),
                   self.directory, max_turns=2, **kwargs)

    def test_exact_text_and_append_only_during_generation(self):
        original = transcript.append
        previous = {}

        def checked_append(path, text):
            path = Path(path)
            before = path.read_bytes() if path.exists() else b''
            self.assertEqual(before, previous.get(path, b''))
            original(path, text)
            after = path.read_bytes()
            self.assertEqual(after, before + text.encode('utf-8'))
            previous[path] = after

        with patch.object(transcript, 'append', checked_append):
            path = self.generate()
        record = read_json(path)
        conversation = record['conversation']
        expected = transcript.field('Opening', conversation['initialization']['output']['beginning_narration']) + '\n'
        for turn in conversation['turns']:
            character = turn['character']['output']
            for label, key in [('Name', 'name'), ('Expression', 'expression'), ('Speech', 'speech'), ('Thought', 'thought')]:
                expected += transcript.field(label, character[key])
            expected += transcript.field('Narration', turn['coordinator']['output']['narration']) + '\n'
        expected += transcript.field('Ending', conversation['ending']['output']['ending_narration'])
        self.assertEqual(path.with_suffix('.txt').read_text(encoding='utf-8'), expected)
        log = path.with_suffix('.log').read_text(encoding='utf-8')
        header, events = log.removeprefix('Input JSON:\n').split('\n\n[', 1)
        self.assertEqual(json.loads(header), self.data)
        for value in ('duration_ms:', 'completion_tokens:', 'runtime.llm_config',
                      'progress.phase: generating_character', 'status: Ended'):
            self.assertIn(value, events)
        self.assertLess(events.index('progress.phase: generating_character'), events.index('status: Ended'))

    def test_multiline_and_empty_values(self):
        class Multiline(MockClient):
            def complete(self, messages, options, schema=None):
                value = super().complete(messages, options, schema)
                if schema == prompts.TURN_SCHEMA:
                    value.update(speech='一行目\nName: 本文中のラベル\n三行目', thought='')
                return value
        path = self.generate(Multiline())
        text = path.with_suffix('.txt').read_text(encoding='utf-8')
        self.assertIn('Speech: 一行目\n  Name: 本文中のラベル\n  三行目\nThought: \n', text)

    def test_invalid_coordinator_leaves_only_opening_and_logs_received_output(self):
        class Invalid(MockClient):
            def complete(self, messages, options, schema=None):
                if 'current_situation' in schema['properties']:
                    return {'narration': '不正な受信結果'}
                return super().complete(messages, options, schema)
        with self.assertRaises(ValueError):
            self.generate(Invalid())
        path = next(self.directory.glob('*.json'))
        text = path.with_suffix('.txt').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('Opening:'))
        self.assertNotIn('\nName:', text)
        log = path.with_suffix('.log').read_text(encoding='utf-8')
        self.assertIn('不正な受信結果', log)
        self.assertIn('status: Failed', log)

    def test_failed_ending_preserves_turns_without_ending_label(self):
        class InvalidEnding(MockClient):
            def complete(self, messages, options, schema=None):
                if schema == prompts.ENDING_SCHEMA:
                    return {'ending_narration': ''}
                return super().complete(messages, options, schema)
        path = self.generate(InvalidEnding())
        text = path.with_suffix('.txt').read_text(encoding='utf-8')
        self.assertEqual(text.count('\nName:'), 2)
        self.assertNotIn('\nEnding:', text)
        log = path.with_suffix('.log').read_text(encoding='utf-8')
        self.assertIn('ending_error:', log)
        self.assertIn('status: Ended', log)

    def test_interrupted_character_records_error_without_partial_turn(self):
        class Interrupted(MockClient):
            def complete(self, messages, options, schema=None):
                if schema == prompts.TURN_SCHEMA:
                    raise KeyboardInterrupt()
                return super().complete(messages, options, schema)
        with self.assertRaises(KeyboardInterrupt):
            self.generate(Interrupted())
        path = next(self.directory.glob('*.json'))
        self.assertNotIn('\nName:', path.with_suffix('.txt').read_text(encoding='utf-8'))
        log = path.with_suffix('.log').read_text(encoding='utf-8')
        self.assertIn('error: interrupted', log)
        self.assertIn('status: Failed', log)
