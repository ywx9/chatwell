import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
from chatwell import jobs
from chatwell.client import MockClient
from chatwell.storage import read_json, save
from chatwell.session import run


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for field, value in [('QUEUE', self.root / 'queue'), ('ROOT', self.root)]:
            patcher = patch.object(jobs, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.input = self.root / 'input.json'
        self.data = read_json(ROOT / 'samples' / 'input.json')
        save(self.input, self.data)

    def test_snapshot_then_three_turn_session(self):
        path = jobs.enqueue(self.input, 3)
        self.input.write_text('{}')
        jobs.process_job(path, MockClient(), {'port': 8080})
        job = read_json(path)
        self.assertEqual(job['status'], 'Ended')
        self.assertEqual(job['input'], self.data)
        session = read_json(job['session_path'])
        self.assertEqual(session['config'], self.data['config'])
        self.assertEqual(len(session['conversation']['turns']), 3)

    def test_input_max_turns_controls_generation(self):
        self.data['config'].pop('goal', None)
        self.data['config']['max_turns'] = 20
        save(self.input, self.data)
        path = jobs.enqueue(self.input)
        self.assertEqual(read_json(path)['max_turns'], 20)
        jobs.process_job(path, MockClient(), {})
        session = read_json(read_json(path)['session_path'])
        self.assertEqual(len(session['conversation']['turns']), 20)
        self.assertEqual(session['conversation']['stop_reason'], 'max_turns_reached')

    def test_cli_max_turns_overrides_input(self):
        self.data['config']['max_turns'] = 20
        save(self.input, self.data)
        path = jobs.enqueue(self.input, 2)
        jobs.process_job(path, MockClient(), {})
        job = read_json(path)
        self.assertEqual(job['max_turns'], 2)
        self.assertEqual(job['input']['config']['max_turns'], 20)
        self.assertEqual(len(read_json(job['session_path'])['conversation']['turns']), 2)

    def test_default_max_turns_without_input_setting(self):
        self.data['config'].pop('max_turns', None)
        save(self.input, self.data)
        self.assertEqual(read_json(jobs.enqueue(self.input))['max_turns'], 12)

    def test_invalid_max_turns_is_rejected_before_enqueue(self):
        for value in (0, -1, True, 1.5, '20', None):
            with self.subTest(value=value):
                self.data['config']['max_turns'] = value
                save(self.input, self.data)
                with self.assertRaisesRegex(ValueError, 'max_turns'):
                    jobs.enqueue(self.input)
        self.assertFalse(list(jobs.QUEUE.glob('*.json')))

    def test_direct_session_uses_config_max_turns(self):
        config = dict(self.data['config'], max_turns=2)
        result = run(config, self.data['characters'], MockClient(), self.root / 'direct')
        self.assertEqual(len(read_json(result)['conversation']['turns']), 2)

    def test_invalid_input_is_not_enqueued(self):
        self.data['config']['llm_config']['reasoning_effort'] = 'none'
        save(self.input, self.data)
        with self.assertRaises(ValueError):
            jobs.enqueue(self.input)
        self.assertFalse(list(jobs.QUEUE.glob('*.json')))

    def test_fifo_and_distinct_requests(self):
        first = jobs.enqueue(self.input)
        second = jobs.enqueue(self.input)
        self.assertNotEqual(first, second)
        self.assertEqual(sorted(jobs.QUEUE.glob('*.json')), [first, second])

    def test_interruption_retains_session_and_pending_then_next_job_runs(self):
        class Interrupted(MockClient):
            def complete(self, messages, options, schema=None):
                if schema and 'progress' in schema['properties']:
                    raise KeyboardInterrupt
                return super().complete(messages, options, schema)
        first = jobs.enqueue(self.input)
        with self.assertRaises(KeyboardInterrupt):
            jobs.process_job(first, Interrupted(), {})
        job = read_json(first)
        self.assertEqual(job['status'], 'Failed')
        session = read_json(job['session_path'])
        self.assertEqual(session['status'], 'Failed')
        self.assertIn('pending_turn', session['conversation'])
        second = jobs.enqueue(self.input, 1)
        jobs.process_job(second, MockClient(), {})
        self.assertEqual(read_json(second)['status'], 'Ended')

    def test_crashed_jobs_are_failed_not_repeated(self):
        path = jobs.enqueue(self.input)
        job = read_json(path)
        job['status'] = 'Running'
        save(path, job)
        jobs.recover()
        self.assertEqual(read_json(path)['status'], 'Failed')

    def test_recovery_preserves_committed_session(self):
        path = jobs.enqueue(self.input, 1)
        jobs.process_job(path, MockClient(), {})
        job = read_json(path)
        job['status'] = 'Running'
        save(path, job)
        jobs.recover()
        self.assertEqual(read_json(path)['status'], 'Ended')

    def test_recovery_finalizes_interrupted_session(self):
        path = jobs.enqueue(self.input, 1)
        session_path = self.root / 'session.json'
        save(session_path, {'status': 'Running', 'timestamps': {'ended': None}, 'conversation': {'pending_turn': {'speech': '途中'}}})
        job = read_json(path)
        job.update(status='Running', session_path=str(session_path))
        save(path, job)
        jobs.recover()
        session = read_json(session_path)
        self.assertEqual(session['status'], 'Failed')
        self.assertIn('pending_turn', session['conversation'])
        self.assertIsNotNone(session['timestamps']['ended'])



    def test_one_failed_request_does_not_prevent_next_request(self):
        first = jobs.enqueue(self.input)
        class Failed(MockClient):
            def complete(self, *args, **kwargs):
                raise ConnectionError('server unavailable')
        jobs.process_job(first, Failed(), {})
        self.assertEqual(read_json(first)['status'], 'Failed')
        second = jobs.enqueue(self.input, 1)
        jobs.process_job(second, MockClient(), {})
        self.assertEqual(read_json(second)['status'], 'Ended')


    def test_style_only_reaches_coordinator_and_effort_is_absent(self):
        calls = []
        class Recording(MockClient):
            def complete(self, messages, options, schema=None):
                calls.append((json.loads(messages[-1]['content']), options, schema))
                return super().complete(messages, options, schema)
        path = jobs.enqueue(self.input, 1)
        jobs.process_job(path, Recording(), {})
        initial, character, update, ending = calls
        style = self.data['config']['coordinator']
        self.assertEqual(initial[0]['coordinator'], style)
        self.assertEqual(update[0]['coordinator'], style)
        self.assertEqual(ending[0]['coordinator'], style)
        self.assertNotIn('coordinator', character[0])
        self.assertNotIn('goal', character[0])
        self.assertTrue(all('reasoning_effort' not in options for _, options, _ in calls))


class TransportSettingsTests(unittest.TestCase):
    def test_environment_inference_settings_and_no_effort_in_payload(self):
        from chatwell.client import LocalClient
        from unittest.mock import MagicMock
        client = LocalClient(inference_options={'chat_template_kwargs': {'enable_thinking': True}, 'reasoning_format': 'auto', 'reasoning_in_content': False}, default_max_tokens=4096)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}', 'reasoning_content': 'private'}}]}).encode()
        client.opener.open = MagicMock(return_value=response)
        self.assertEqual(client.complete([], {'top_p': .95}, {'type': 'object'}), {'ok': True})
        payload = json.loads(client.opener.open.call_args.args[0].data)
        self.assertNotIn('reasoning_effort', payload)
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertTrue(payload['chat_template_kwargs']['enable_thinking'])
        self.assertFalse(payload['reasoning_in_content'])
