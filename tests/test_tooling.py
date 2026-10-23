import argparse
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from chatwell import tooling
from chatwell.local_config import read_values
from chatwell.managed import ensure_server, stop_server, runtime, health, server_command
from chatwell.settings import load
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class ToolingTests(unittest.TestCase):
    def test_check_discovers_single_model_and_preserves_custom_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'llm').mkdir()
            (base / 'llm/model.gguf').write_bytes(b'GGUF')
            path = base / '.env'
            with patch('chatwell.tooling.ensure_server'), patch('chatwell.__main__.main', return_value=0), redirect_stdout(io.StringIO()):
                self.assertEqual(tooling.check(path), 0)
            values = read_values(path)
            self.assertEqual(values['SELF_MANAGED_LLAMA'], 'true')
            self.assertEqual(values['LOCAL_GGUF_PATH'], 'llm/model.gguf')
            path.write_text('SELF_MANAGED_LLAMA=true\nLOCAL_GGUF_PATH=../custom.gguf\nCHATWELL_MAX_TOKENS=99\n')
            original = path.read_text()
            with patch('chatwell.tooling.ensure_server'), patch('chatwell.__main__.main', return_value=0), redirect_stdout(io.StringIO()):
                self.assertEqual(tooling.check(path), 0)
            self.assertEqual(path.read_text(), original)

    def test_check_missing_and_multiple_models_do_not_choose(self):
        for count in (0, 2):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / 'llm').mkdir()
                for i in range(count):
                    (base / f'llm/model{i}.gguf').write_bytes(b'GGUF')
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(tooling.check(base / '.env'), 1)
                self.assertEqual(read_values(base / '.env')['LOCAL_GGUF_PATH'], '')

    def test_enqueue_requires_settings_before_creating_session(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                tooling.enqueue(ROOT / 'samples/input.json', Path(directory) / '.env', directory)
            self.assertFalse((Path(directory) / 'sessions').exists())

    def test_detached_worker_starts_processes_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            settings_path = base / '.env'
            settings_path.write_text('SELF_MANAGED_LLAMA=false\n')
            try:
                with redirect_stdout(io.StringIO()):
                    path = tooling.enqueue(ROOT / 'samples/input.json', settings_path, base, 2, mock=True)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and read_json(path)['status'] not in ('Ended', 'Failed'):
                    time.sleep(.05)
                result = read_json(path)
                self.assertEqual(result['status'], 'Ended')
                self.assertEqual(len(result['conversation']['turns']), 2)
                with self.assertRaisesRegex(RuntimeError, '設定が異なります'):
                    tooling.enqueue(ROOT / 'samples/input.json', settings_path, base, 2, mock=False)
                self.assertEqual(len(list((base / 'sessions').glob('*.json'))), 1)
            finally:
                with redirect_stdout(io.StringIO()):
                    tooling.stop_worker(base)
                from chatwell.locking import worker_lock
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with worker_lock(base / '.runtime/worker.lock'):
                            break
                    except RuntimeError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(.1)

    def test_user_test_command_with_external_http_inference(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from chatwell.client import MockClient
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"build_info":"fixture-build"}')
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(payload)
                schema = payload['response_format']['json_schema']['schema']
                output = MockClient().complete(payload['messages'], payload, schema)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'model': 'fixture-gemma', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20},
                    'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]}).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                settings_path = base / '.env'
                settings_path.write_text(f'SELF_MANAGED_LLAMA=false\nCHATWELL_ENDPOINT=http://127.0.0.1:{server.server_port}\n')
                try:
                    result = subprocess.run([sys.executable, str(ROOT / 'tools/test.py'), '--settings', str(settings_path),
                        '--data-dir', directory, '--wait-timeout', '30'], cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=40)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('テスト成功', result.stdout)
                    self.assertEqual(len(requests), 8)
                    record = read_json(next((base / 'sessions').glob('*.json')))
                    self.assertEqual(record['runtime']['mode'], 'model')
                    self.assertEqual(record['runtime']['response_model'], 'fixture-gemma')
                    self.assertEqual(record['runtime']['server']['llama_cpp_version'], 'fixture-build')
                    self.assertEqual(record['runtime']['server']['source'], 'server_props')
                    self.assertEqual(record['conversation']['turns'][0]['character']['metrics']['completion_tokens'], 20)
                finally:
                    with redirect_stdout(io.StringIO()):
                        tooling.stop_worker(base)
                    from chatwell.locking import worker_lock
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            with worker_lock(base / '.runtime/worker.lock'):
                                break
                        except RuntimeError:
                            if time.monotonic() > deadline:
                                raise
                            time.sleep(.1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    @unittest.skipIf(os.name == 'nt', 'POSIX executable fixture; portable worker is tested on all platforms')
    def test_managed_server_real_subprocess_health_auth_provenance_and_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'llama').mkdir()
            (base / 'llm').mkdir()
            model = base / 'llm/test.gguf'
            model.write_bytes(b'GGUFfixture-not-a-real-model')
            executable = base / 'llama/llama-server'
            executable.write_text('#!' + sys.executable + '\n' + '''import argparse, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
if '--version' in sys.argv:
    print('version: fixture-1')
    raise SystemExit(0)
p=argparse.ArgumentParser()
p.add_argument('--port',type=int)
a,_=p.parse_known_args()
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer '+os.environ['LLAMA_API_KEY']:
            self.send_response(401); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok"}')
    def do_POST(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}).encode())
    def log_message(self,*args): pass
HTTPServer(('127.0.0.1',a.port),H).serve_forever()
''')
            executable.chmod(0o755)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            path = base / '.env'
            path.write_text(f'SELF_MANAGED_LLAMA=true\nLOCAL_GGUF_PATH=llm/test.gguf\nCHATWELL_ENDPOINT=http://127.0.0.1:{port}\nLOCAL_LLAMA_START_TIMEOUT=15\n')
            with patch.dict(os.environ, {'CHATWELL_API_KEY': 'fixture-only-key'}):
                settings = load(argparse.Namespace(settings=str(path)))
                try:
                    ensure_server(path, settings)
                    self.assertTrue(health(settings['endpoint'], settings['api_key']))
                    state = read_json(runtime(path) / 'server.json')
                    self.assertEqual(state['provenance']['model_file'], 'test.gguf')
                    self.assertEqual(len(state['provenance']['model_sha256']), 64)
                    self.assertEqual(state['provenance']['llama_cpp_version'], 'version: fixture-1')
                    self.assertNotIn('fixture-only-key', (runtime(path) / 'server.json').read_text())
                    ensure_server(path, settings)
                    self.assertEqual(read_json(runtime(path) / 'server.json')['instance'], state['instance'])
                finally:
                    stop_server(path)
                self.assertFalse(health(settings['endpoint'], settings['api_key']))
                self.assertFalse(stop_server(path))
