import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chatwell import jobs
from chatwell.client import MockClient
from chatwell.records import SCHEMA_VERSION
from chatwell.session import run
from chatwell.storage import read_json, save
from chatwell.tooling import monitor, status

ROOT = Path(__file__).resolve().parents[1]


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = read_json(ROOT / 'samples/input.json')

    def generate(self, client=None, turns=2):
        return run(self.data['config'], self.data['characters'], client or MockClient(), self.directory, max_turns=turns)

    def test_schema_effective_settings_metrics_and_filename(self):
        class Timed(MockClient):
            default_max_tokens = 2048
            model = 'requested-alias'
            def complete(self, *args):
                result = super().complete(*args)
                self.last_metrics = {'prompt_tokens': 123, 'completion_tokens': 45, 'finish_reason': 'stop'}
                self.response_model = 'reported-model'
                return result
        path = self.generate(Timed())
        self.assertRegex(path.name, r'^\d{8}-\d{6}-\d{3}Z-[0-9a-f]{12}\.json$')
        data = read_json(path)
        self.assertEqual(data['schema_version'], SCHEMA_VERSION)
        self.assertEqual(data['chatwell_version'], '0.3.1')
        self.assertEqual(data['config']['max_turns'], 20)
        self.assertEqual(data['runtime']['max_turns'], 2)
        self.assertEqual(data['runtime']['llm_config']['max_tokens'], 2048)
        self.assertEqual(data['runtime']['response_model'], 'reported-model')
        self.assertEqual(data['progress'], {'phase': 'ended', 'completed_turns': 2})
        c = data['conversation']
        self.assertEqual([t['index'] for t in c['turns']], [0, 1])
        records = [c['initialization'], c['ending']]
        for turn in c['turns']:
            self.assertEqual(set(turn), {'index', 'character', 'coordinator'})
            records.extend([turn['character'], turn['coordinator']])
        for record in records:
            self.assertIsInstance(record['output'], dict)
            self.assertGreaterEqual(record['metrics']['duration_ms'], 0)
            self.assertEqual(record['metrics']['prompt_tokens'], 123)
            self.assertEqual(record['metrics']['completion_tokens'], 45)
            self.assertEqual(record['metrics']['finish_reason'], 'stop')
            self.assertTrue(record['metrics']['started'])
            self.assertTrue(record['metrics']['ended'])
        self.assertNotIn('endpoint', data['runtime'])
        self.assertNotIn('api_key', data['runtime'])

    def test_immediate_session_path_and_no_replacement_when_processed(self):
        jobs.configure(self.directory)
        job_path = jobs.enqueue(ROOT / 'samples/input.json', 2)
        session_path = Path(read_json(job_path)['session_path'])
        self.assertTrue(session_path.exists())
        queued = read_json(session_path)
        self.assertEqual(queued['status'], 'Queued')
        self.assertEqual(queued['runtime']['max_turns'], 2)
        jobs.work(MockClient(), {}, once=True)
        ended = read_json(session_path)
        self.assertEqual(ended['id'], queued['id'])
        self.assertEqual(ended['timestamps']['queued'], queued['timestamps']['queued'])
        self.assertEqual(ended['status'], 'Ended')
        self.assertEqual(len(list((self.directory / 'sessions').glob('*.json'))), 1)

    def test_failed_call_retains_timing_and_pending_record(self):
        class Failed(MockClient):
            def complete(self, messages, options, schema=None):
                if 'speech' in schema['properties']:
                    raise TimeoutError('slow')
                return super().complete(messages, options, schema)
        with self.assertRaises(TimeoutError):
            self.generate(Failed())
        data = read_json(next(self.directory.glob('*.json')))
        self.assertEqual(data['status'], 'Failed')
        record = data['conversation']['pending_turn']['character']
        self.assertIsNone(record['output'])
        self.assertEqual(record['error'], 'TimeoutError')
        self.assertGreaterEqual(record['metrics']['duration_ms'], 0)

    def test_monitor_success_failure_timeout_and_ending_error(self):
        path = self.generate()
        data = read_json(path)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(monitor(path), 0)
        self.assertIn('テスト成功', output.getvalue())
        for mutation in ('failed', 'ending_error', 'empty_turns'):
            record = copy.deepcopy(data)
            if mutation == 'failed':
                record['status'] = 'Failed'
            elif mutation == 'ending_error':
                record['conversation']['ending_error'] = 'bad closing'
            else:
                record['conversation']['turns'] = []
            save(path, record)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(monitor(path), 1)
        data['status'] = 'Running'
        save(path, data)
        with patch('chatwell.tooling.time.monotonic', side_effect=[0, 3601]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(monitor(path), 2)
        self.assertIn('停止していません', output.getvalue())
        self.assertEqual(read_json(path)['status'], 'Running')

    def test_worker_stop_preserves_remaining_queued_requests(self):
        jobs.configure(self.directory)
        first = jobs.enqueue(ROOT / 'samples/input.json', 1)
        second = jobs.enqueue(ROOT / 'samples/input.json', 1)
        jobs.work(MockClient(), {}, should_stop=lambda: read_json(first)['status'] == 'Ended')
        self.assertEqual(read_json(first)['status'], 'Ended')
        self.assertEqual(read_json(second)['status'], 'Queued')
        self.assertEqual(read_json(read_json(second)['session_path'])['status'], 'Queued')

    def test_status_rejects_non_sessions(self):
        path = self.directory / 'invalid.json'
        save(path, {'status': 'Ended'})
        with self.assertRaises(ValueError):
            status(path)
