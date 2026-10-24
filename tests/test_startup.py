"""Exercise tools/run.py, real HTTP requests and SIGINT without a real model."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == 'nt', 'POSIX SIGINT process lifecycle')
class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = self.root / '.env'
        self.settings.write_text('', encoding='utf-8')
        self.data = read_json(ROOT / 'samples/input.json')
        self.data['config']['max_turns'] = 1
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(('CHATWELL_', 'LLM_'))}
        self.environment['PYTHONUNBUFFERED'] = '1'
        self.environment['PYTHONUTF8'] = '1'

    def request(self, port, path, data=None):
        request = urllib.request.Request(
            f'http://127.0.0.1:{port}' + path,
            None if data is None else json.dumps(data).encode(),
            {'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(request, timeout=2)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def start(self, mock=True):
        log = tempfile.TemporaryFile(mode='w+b')
        self.addCleanup(log.close)
        command = [sys.executable, str(ROOT / 'tools/run.py'), '--settings', str(self.settings), '--port', '0']
        if mock:
            command.append('--mock')
        process = subprocess.Popen(command, cwd=tempfile.gettempdir(), env=self.environment,
                                   stdout=log, stderr=log)
        def cleanup():
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            log.seek(0)
            content = log.read().decode('utf-8', errors='replace')
            match = re.search(r'http://127\.0\.0\.1:(\d+)', content)
            if match:
                port = int(match[1])
                self.assertEqual(self.request(port, '/healthz')[0], 200)
                return process, port
            if process.poll() is not None:
                self.fail('startup failed: ' + content)
            time.sleep(.03)
        self.fail('startup timed out: ' + content)

    def stop(self, process):
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(timeout=3), 0)

    def wait_result(self, port, url):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, result = self.request(port, url + '/result')
            if status == 200:
                return result
            self.assertEqual(status, 202)
            time.sleep(.03)
        self.fail('generation timed out')

    def test_stays_alive_when_queue_empty_and_accepts_later_requests(self):
        self.settings.unlink()
        process, port = self.start()
        time.sleep(.15)
        self.assertIsNone(process.poll())
        identities = []
        for _ in range(2):
            status, record = self.request(port, '/v1/sessions', self.data)
            self.assertEqual(status, 202)
            identities.append(record['id'])
            self.assertEqual(self.wait_result(port, record['url'])['status'], 'Ended')
            self.assertEqual(self.request(port, record['url'] + '/turns/0')[0], 200)
            time.sleep(.15)
            self.assertIsNone(process.poll())
        self.assertEqual(len(set(identities)), 2)
        self.stop(process)
        # Shutdown releases the single-process lock and the same data is readable.
        process, port = self.start()
        self.assertEqual(self.request(port, '/v1/sessions/' + identities[0])[1]['status'], 'Ended')
        self.stop(process)

    def test_ctrl_c_interrupts_llm_wait_and_preserves_unstarted_queue(self):
        started = threading.Event()
        release = threading.Event()
        class BlockingLLM(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                started.set()
                release.wait(10)
                self.send_response(503)
                self.end_headers()

            def log_message(self, *args):
                pass
        llm = ThreadingHTTPServer(('127.0.0.1', 0), BlockingLLM)
        thread = threading.Thread(target=llm.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(llm.server_close)
        self.addCleanup(llm.shutdown)
        self.addCleanup(release.set)
        with self.settings.open('a', encoding='utf-8') as stream:
            stream.write(f'LLM_ENDPOINT=http://127.0.0.1:{llm.server_port}\n'
                         'LLM_REQUEST_TIMEOUT=3600\n')
        process, port = self.start(mock=False)
        _, first = self.request(port, '/v1/sessions', self.data)
        self.assertTrue(started.wait(3), 'LLM request never started')
        self.assertEqual(self.request(port, first['url'] + '/turns/0')[0], 202)
        _, second = self.request(port, '/v1/sessions', self.data)
        self.stop(process)  # Must finish while the upstream request is still blocked.
        records = {record['id']: record for record in
                   map(read_json, (self.root / 'data/sessions').glob('*.json'))}
        self.assertEqual(records[first['id']]['status'], 'Failed')
        self.assertEqual(records[second['id']]['status'], 'Queued')
        self.assertEqual(records[first['id']]['conversation']['stop_reason'], 'user_interrupted')
        release.set()
        # Restart consumes the waiting job without repeating the interrupted job.
        process, port = self.start(mock=True)
        self.assertEqual(self.wait_result(port, second['url'])['status'], 'Ended')
        self.assertEqual(self.request(port, first['url'])[1]['status'], 'Failed')
        self.assertEqual(len(list((self.root / 'data/sessions').glob('*.json'))), 2)
        self.stop(process)

    def test_second_process_cannot_use_same_queue(self):
        process, _ = self.start()
        attempt = subprocess.run([sys.executable, str(ROOT / 'tools/run.py'), '--settings', str(self.settings),
                                  '--mock', '--port', '0'], cwd=tempfile.gettempdir(),
                                 env=self.environment, capture_output=True, timeout=3)
        self.assertEqual(attempt.returncode, 1)
        self.assertIn('既に動作', attempt.stderr.decode())
        self.assertIsNone(process.poll())
        self.stop(process)
