import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from chatwell import prompts
from chatwell.client import MockClient
from chatwell.session import run
from chatwell.storage import save
from session_view import read_json

ROOT = Path(__file__).resolve().parents[1]
from chatwell import jobs
from unittest.mock import patch


class EndingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        data = read_json(ROOT / 'samples/input.json')
        self.config = data['config']
        self.characters = data['characters']

    def client(self, outcome=None):
        class Recording(MockClient):
            def __init__(self):
                self.calls = []
            def complete(self, messages, options, schema=None):
                data = json.loads(messages[-1]['content'])
                self.calls.append((copy.deepcopy(data), schema))
                if schema == prompts.ENDING_SCHEMA and outcome is not None:
                    if isinstance(outcome, BaseException):
                        raise outcome
                    return outcome
                return super().complete(messages, options, schema)
        return Recording()

    def test_closing_once_for_both_stop_reasons_and_no_goal(self):
        for turns, goal, reason in [(3, True, 'goal_achieved'), (1, True, 'max_turns_reached'), (2, False, 'max_turns_reached')]:
            with self.subTest(turns=turns, goal=goal):
                config = copy.deepcopy(self.config)
                if not goal:
                    config.pop('goal', None)
                client = self.client()
                result = read_json(run(config, self.characters, client, self.root, max_turns=turns))
                conversation = result['conversation']
                ending_calls = [d for d, schema in client.calls if schema == prompts.ENDING_SCHEMA]
                self.assertEqual(len(ending_calls), 1)
                ending = ending_calls[0]
                self.assertEqual(client.calls[-1][1], prompts.ENDING_SCHEMA)
                self.assertEqual(ending['stop_reason'], reason)
                self.assertEqual(conversation['stop_reason'], reason)
                self.assertEqual(len(conversation['turns']), turns)
                self.assertEqual(len(ending['history']), turns)
                self.assertEqual(ending['coordinator'], config['coordinator'])
                self.assertEqual(ending['current_situation'], conversation['turns'][-1]['current_situation'])
                self.assertEqual(ending['history'][-1]['narration'], conversation['turns'][-1]['narration'])
                for turn in ending['history']:
                    self.assertEqual(set(turn), {'name', 'speech', 'expression', 'narration'})
                self.assertNotIn('goal', ending)
                self.assertTrue(conversation['ending_narration'])
                self.assertNotIn('ending_error', conversation)
                self.assertNotIn('pending_ending', conversation)
                self.assertEqual(result['status'], 'Ended')

    def test_closing_failure_preserves_completed_turns_and_reason(self):
        for outcome in (TimeoutError('closing timeout'), ValueError('truncated response'),
                        {'ending_narration': ''}, {'ending_narration': 42}, {'wrong': 'field'}):
            with self.subTest(outcome=outcome):
                client = self.client(outcome)
                result = read_json(run(self.config, self.characters, client, self.root, max_turns=1))
                conversation = result['conversation']
                self.assertEqual(result['status'], 'Ended')
                self.assertEqual(conversation['stop_reason'], 'max_turns_reached')
                self.assertEqual(len(conversation['turns']), 1)
                self.assertEqual(conversation['ending_narration'], '')
                self.assertTrue(conversation['ending_error'])
                self.assertIsNotNone(result['timestamps']['ended'])
                if isinstance(outcome, dict):
                    self.assertEqual(conversation['pending_ending'], outcome)

    def test_no_closing_after_body_failure_or_interrupt(self):
        for error in (TimeoutError('body timeout'), KeyboardInterrupt()):
            client = self.client()
            original = client.complete
            def fail(messages, options, schema=None):
                if schema == prompts.TURN_SCHEMA:
                    raise error
                return original(messages, options, schema)
            client.complete = fail
            with self.assertRaises(type(error)):
                run(self.config, self.characters, client, self.root, max_turns=1)
            self.assertFalse(any(schema == prompts.ENDING_SCHEMA for _, schema in client.calls))

    def test_stop_during_closing_preserves_ended_queue_and_session(self):
        input_path = self.root / 'input.json'
        save(input_path, {'config': self.config, 'characters': self.characters})
        with patch.object(jobs, 'QUEUE', self.root / 'queue'), patch.object(jobs, 'ROOT', self.root):
            path = jobs.enqueue(input_path, 1)
            with self.assertRaises(KeyboardInterrupt):
                jobs.process_job(path, self.client(KeyboardInterrupt()), {})
        job = read_json(path)
        session = read_json(job['session_path'])
        self.assertEqual(job['status'], 'Ended')
        self.assertEqual(session['status'], 'Ended')
        self.assertEqual(session['conversation']['ending_error'], 'user_interrupted')
        self.assertEqual(session['conversation']['stop_reason'], 'max_turns_reached')
        self.assertEqual(len(session['conversation']['turns']), 1)

    def test_queue_marks_body_success_even_if_closing_times_out(self):
        input_path = self.root / 'input.json'
        save(input_path, {'config': self.config, 'characters': self.characters})
        with patch.object(jobs, 'QUEUE', self.root / 'queue'), patch.object(jobs, 'ROOT', self.root):
            path = jobs.enqueue(input_path, 1)
            jobs.process_job(path, self.client(TimeoutError('closing timeout')), {})
        job = read_json(path)
        self.assertEqual(job['status'], 'Ended')
        self.assertIn('TimeoutError', read_json(job['session_path'])['conversation']['ending_error'])


    def test_recovery_after_crash_during_closing_keeps_body_complete(self):
        input_path = self.root / 'input.json'
        save(input_path, {'config': self.config, 'characters': self.characters})
        with patch.object(jobs, 'QUEUE', self.root / 'queue'), patch.object(jobs, 'ROOT', self.root):
            path = jobs.enqueue(input_path, 1)
            jobs.process_job(path, self.client(), {})
            job = read_json(path)
            from chatwell.storage import read_json as read_record
            session = read_record(job['session_path'])
            job.update(status='Running', ended=None)
            session['status'] = 'Running'
            session['timestamps']['ended'] = None
            session['conversation']['ending']['output']['ending_narration'] = ''
            save(path, job)
            save(job['session_path'], session)
            jobs.recover()
        self.assertEqual(read_json(path)['status'], 'Ended')
        result = read_json(job['session_path'])
        self.assertEqual(result['status'], 'Ended')
        self.assertEqual(result['conversation']['stop_reason'], 'max_turns_reached')
        self.assertEqual(result['conversation']['ending_error'], 'worker_interrupted')
