import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

from chatwell import validation
from chatwell.client import LlamaClient, MockClient
from chatwell.session import run
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class ReasoningBudgetTests(unittest.TestCase):
    def setUp(self):
        self.data = read_json(ROOT / 'samples/input.json')

    def test_budget_validation_and_omission(self):
        config = copy.deepcopy(self.data['config'])
        for value in (-1, 0, 2048):
            with self.subTest(valid=value):
                config['llm_config']['reasoning_budget_tokens'] = value
                validation.request(config, self.data['characters'])
        for value in (-2, True, False, 2048.0, '2048', None, float('nan')):
            with self.subTest(invalid=value):
                config['llm_config']['reasoning_budget_tokens'] = value
                with self.assertRaises(ValueError):
                    validation.request(config, self.data['characters'])
        config['llm_config'].pop('reasoning_budget_tokens')
        validation.request(config, self.data['characters'])
        config['llm_config']['thinking_budget'] = 2048
        with self.assertRaises(ValueError):
            validation.request(config, self.data['characters'])

    def test_transport_preserves_zero_and_does_not_leak_budget(self):
        client = LlamaClient()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]
        }).encode()
        client.opener.open = MagicMock(return_value=response)
        for options in ({'reasoning_budget_tokens': 0}, {'reasoning_budget_tokens': -1}, {}):
            client.complete([], options, {'type': 'object'})
            payload = json.loads(client.opener.open.call_args.args[0].data)
            if options:
                self.assertEqual(payload['reasoning_budget_tokens'], options['reasoning_budget_tokens'])
            else:
                self.assertNotIn('reasoning_budget_tokens', payload)
            self.assertNotIn('thinking_budget', payload)

    def test_every_generation_stage_receives_budget_over_http(self):
        received = []
        fixture = MockClient()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(body)
                output = fixture.complete(body['messages'], {}, body['response_format']['json_schema']['schema'])
                response = json.dumps({'choices': [{'finish_reason': 'stop',
                                                    'message': {'content': json.dumps(output)}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = copy.deepcopy(self.data['config'])
            config['llm_config'].update(reasoning_budget_tokens=2048, max_tokens=6144)
            client = LlamaClient(f'http://127.0.0.1:{server.server_port}',
                                 inference_options={'chat_template_kwargs': {'enable_thinking': True}})
            with tempfile.TemporaryDirectory() as directory:
                result = read_json(run(config, self.data['characters'], client, directory, max_turns=2))
            self.assertEqual(result['status'], 'Ended')
            self.assertEqual(result['runtime']['llm_config']['reasoning_budget_tokens'], 2048)
            self.assertEqual(len(received), 6)  # Initialization + two pairs of turns + ending.
            for body in received:
                self.assertEqual(body['reasoning_budget_tokens'], 2048)
                self.assertEqual(body['max_tokens'], 6144)
                self.assertTrue(body['chat_template_kwargs']['enable_thinking'])
                self.assertNotIn('thinking_budget', body)
                self.assertNotIn('reasoning_budget_tokens', body['chat_template_kwargs'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
