import copy
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from chatwell.client import LocalClient, MockClient
from chatwell.session import run
from session_view import read_json
from chatwell import validation

ROOT = Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(ROOT / 'tests/fixtures/examples/config.json')
        self.characters = [read_json(ROOT / 'tests/fixtures/examples' / f'{n}.json') for n in ('alice', 'bob')]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_template_contract_and_atomic_snapshots(self):
        snapshots = []
        client = MockClient()
        original = client.complete
        inputs = []
        systems = []

        def capture(messages, options, schema):
            inputs.append(json.loads(messages[-1]['content']))
            systems.append(messages[0]['content'])
            return original(messages, options, schema)

        client.complete = capture
        path = run(self.config, self.characters, client, self.temp.name,
                   lambda p: snapshots.append(read_json(p)))
        result = read_json(path)
        states = [s['status'] for s in snapshots]
        self.assertEqual(states[:3], ['Queued', 'Starting', 'Starting'])
        self.assertEqual(list(dict.fromkeys(states)), ['Queued', 'Starting', 'Running', 'Ended'])
        self.assertEqual(states[-1], 'Ended')
        self.assertEqual(set(result), set(read_json(ROOT / 'tests/fixtures/templates/chatwell.json')))
        self.assertEqual(set(result['config']), set(read_json(ROOT / 'tests/fixtures/templates/config.json')))
        self.assertEqual(set(result['conversation']), set(read_json(ROOT / 'tests/fixtures/templates/conversation.json')))
        self.assertEqual(result['conversation']['turns'][0]['character']['name'], 'Alice')
        self.assertIn('ですね', result['conversation']['turns'][0]['character']['speech'])
        self.assertEqual(inputs[1]['non_participants'], ['Charlie'])
        self.assertNotIn('character', inputs[1])
        self.assertIn(json.dumps({k: v for k, v in self.characters[0].items() if k != 'profile'}, ensure_ascii=False), systems[1])
        self.assertEqual(inputs[1]['language'], 'Japanese')
        self.assertIsNone(snapshots[0]['timestamps']['started'])
        for snapshot in snapshots[1:-1]:
            self.assertIsNotNone(snapshot['timestamps']['started'])
            self.assertIsNone(snapshot['timestamps']['ended'])
        self.assertIsNotNone(result['timestamps']['ended'])
        self.characters[0]['name'] = 'Changed'
        self.assertEqual(result['characters'][0]['name'], 'Alice')
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 1)

    def test_failures_are_finalized(self):
        for error in (TimeoutError('timeout'), ValueError('bad JSON'), ConnectionRefusedError('offline')):
            with self.subTest(error=error), patch.object(MockClient, 'complete', side_effect=error):
                with self.assertRaises(type(error)):
                    run(self.config, self.characters, MockClient(), self.temp.name)
        for path in Path(self.temp.name).glob('*.json'):
            result = read_json(path)
            self.assertEqual(result['status'], 'Failed')
            self.assertIsNotNone(result['timestamps']['ended'])
            self.assertTrue(result['conversation']['stop_reason'].startswith('error:'))

    def test_bad_turn_preserves_beginning(self):
        with patch.object(MockClient, 'complete', side_effect=[
            {'situation': 'Cafe', 'goal': 'Comfort', 'non_participants': [], 'beginning_narration': '始まり'},
            {'name': 'Charlie', 'speech': 'No'},
        ]):
            with self.assertRaises(ValueError):
                run(self.config, self.characters, MockClient(), self.temp.name)
        result = read_json(next(Path(self.temp.name).glob('*.json')))
        self.assertEqual(result['status'], 'Failed')
        self.assertEqual(result['conversation']['beginning_narration'], '始まり')
        self.assertEqual(result['conversation']['turns'], [])

    def test_validation(self):
        for mutation in ('duplicate', 'blank', 'override'):
            config, characters = copy.deepcopy(self.config), copy.deepcopy(self.characters)
            if mutation == 'duplicate':
                characters[1]['name'] = 'Alice'
            elif mutation == 'blank':
                config['language'] = ''
            else:
                config['llm_config']['messages'] = []
            with self.assertRaises(ValueError):
                validation.request(config, characters)


class TransportTests(unittest.TestCase):
    def test_http_timeout_marks_failed(self):
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                release.wait(2)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(TimeoutError):
                    run(read_json(ROOT / 'tests/fixtures/examples/config.json'), [read_json(ROOT / 'tests/fixtures/examples/alice.json')],
                        LocalClient(f'http://127.0.0.1:{server.server_port}', timeout=0.05), directory)
                result = read_json(next(Path(directory).glob('*.json')))
                self.assertEqual(result['status'], 'Failed')
                self.assertIsNotNone(result['timestamps']['ended'])
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_mock_plain_probe(self):
        self.assertEqual(MockClient().complete([{'role': 'user', 'content': 'Hello'}], {}),
                         'Hello from the mock fixture.')

    def test_endpoint_validation(self):
        for url in ('ftp://example.com', 'http://', 'http://127.0.0.1@evil.test', 'http://localhost?x=y', 'http://localhost:bad'):
            with self.assertRaises(ValueError):
                LocalClient(url)

    def test_http_payload_and_response_validation(self):
        received = []
        replies = [json.dumps({'choices': [{'message': {'content': '{"speech":"こんにちは"}'}, 'finish_reason': 'stop'}]}).encode(),
                   b'{"choices":[{"message":{"content":"not JSON"}}]}',
                   b'{"choices":[{"message":{"content":"{}"},"finish_reason":"length"}]}',
                   b'{"choices":[{"message":{"content":"{\\"confidence\\":NaN}"}}]}']

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(replies.pop(0))

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = LocalClient(f'http://127.0.0.1:{server.server_port}')
            schema = {'type': 'object'}
            self.assertEqual(client.complete([], {'max_tokens': 50}, schema), {'speech': 'こんにちは'})
            self.assertEqual(received[0][0], '/v1/chat/completions')
            self.assertEqual(received[0][1]['response_format']['json_schema']['schema'], schema)
            for _ in range(3):
                with self.assertRaises(ValueError):
                    client.complete([], {}, schema)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_real_connection_refusal_marks_failed(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                run(read_json(ROOT / 'tests/fixtures/examples/config.json'), [read_json(ROOT / 'tests/fixtures/examples/alice.json')],
                    LocalClient(f'http://127.0.0.1:{port}', timeout=0.5), directory)
            self.assertEqual(read_json(next(Path(directory).glob('*.json')))['status'], 'Failed')


if __name__ == '__main__':
    unittest.main()
