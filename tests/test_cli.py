import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from chatwell.client import LlamaClient
from chatwell.locking import worker_lock
from chatwell.settings import inference_settings, read_settings
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class ClientSettingsTests(unittest.TestCase):
    def test_settings_precedence_and_no_shell_expansion(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'settings.env'
            path.write_text('LLM_ENDPOINT=https://file.test\nLLM_MODEL=$(not-a-command)\n'
                            'LLM_MAX_TOKENS=2048\n', encoding='utf-8')
            self.assertEqual(inference_settings(read_settings(path))['endpoint'], 'https://file.test')
            self.assertEqual(inference_settings(read_settings(path))['model'], '$(not-a-command)')
            with patch.dict(os.environ, {'LLM_ENDPOINT': 'https://environment.test'}):
                self.assertEqual(inference_settings(read_settings(path))['endpoint'], 'https://environment.test')
            self.assertEqual(inference_settings(read_settings(path))['default_max_tokens'], 2048)

    def test_invalid_settings_and_inputs_fail(self):
        with patch.dict(os.environ, {}, clear=True):
            for value in ('nan', '0', '-1'):
                with self.assertRaises(ValueError):
                    inference_settings({**read_settings(None), 'LLM_REQUEST_TIMEOUT': value})
            with patch.dict(os.environ, {'LLM_ENABLE_THINKING': 'maybe'}):
                with self.assertRaises(ValueError):
                    inference_settings(read_settings(None))

    def test_worker_lock_excludes_another_process_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.lock'
            code = ('from pathlib import Path; from chatwell.locking import worker_lock; '
                    'import sys\nwith worker_lock(Path(sys.argv[1])): pass')
            with worker_lock(path):
                attempt = subprocess.run([sys.executable, '-c', code, str(path)], cwd=ROOT, capture_output=True)
                self.assertNotEqual(attempt.returncode, 0)
                self.assertIn(b'RuntimeError', attempt.stderr)
            attempt = subprocess.run([sys.executable, '-c', code, str(path)], cwd=ROOT, capture_output=True)
            self.assertEqual(attempt.returncode, 0, attempt.stderr)

    def test_auth_against_http_server(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path, self.headers.get('Authorization'), data))
                if self.headers.get('Authorization') != 'Bearer test-only-key':
                    self.send_response(401)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'choices': [{'finish_reason': 'stop',
                                                          'message': {'content': '{"ok":true}'}}]}).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/v1'
            from urllib.error import HTTPError
            client = LlamaClient(url, api_key='test-only-key', model='gemma',
                                 inference_options={'reasoning_in_content': False})
            self.assertEqual(client.complete([{'role': 'user', 'content': 'Return JSON'}], {},
                                             {'type': 'object'}), {'ok': True})
            self.assertEqual(received[0][0], '/v1/chat/completions')
            self.assertEqual(received[0][2]['model'], 'gemma')
            self.assertFalse(received[0][2]['reasoning_in_content'])
            with self.assertRaises(HTTPError) as error:
                LlamaClient(url).complete([], {})
            self.assertEqual(error.exception.code, 401)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_worker_recovers_interrupted_job_before_session_creation(self):
        from chatwell import jobs
        from chatwell.client import MockClient
        from chatwell.storage import save
        with tempfile.TemporaryDirectory() as directory:
            jobs.configure(directory)
            path = jobs.enqueue(ROOT / 'samples/input.json', 1)
            def interrupt(*args):
                job = read_json(path)
                job['status'] = 'Running'
                save(path, job)
                raise KeyboardInterrupt
            with patch.object(jobs, 'process_job', side_effect=interrupt):
                jobs.work(MockClient(), {}, once=True)
            self.assertEqual(read_json(path)['status'], 'Failed')
            # No stale ownership remains after interruption.
            with worker_lock(Path(directory) / '.runtime/worker.lock'):
                pass

    def test_malformed_transport_response_is_actionable(self):
        from unittest.mock import MagicMock
        client = LlamaClient()
        for reply in ({'choices': []}, {'choices': [None]}, {'unexpected': True}):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(reply).encode()
            client.opener.open = MagicMock(return_value=response)
            with self.assertRaisesRegex(ValueError, 'Invalid chat/completions response'):
                client.complete([], {})

    def test_remote_urls_and_redirect_protection(self):
        for url in ('https://inference.example/v1', 'http://192.168.1.2:8080', 'http://[::1]:8080'):
            self.assertTrue(LlamaClient(url).url.endswith('/v1'))
        from chatwell.client import NoRedirect
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')
