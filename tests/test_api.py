import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from chatwell import jobs
from chatwell.api import Application, make_server
from chatwell.client import MockClient
from chatwell.storage import read_json, save

ROOT = Path(__file__).resolve().parents[1]


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Application(self.root)
        self.server = make_server('127.0.0.1', 0, self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.data = read_json(ROOT / 'samples/input.json')
        self.data['config']['max_turns'] = 3

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, *, headers=None, raw=None):
        body = json.dumps(data).encode() if data is not None else raw
        request_headers = {}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        req = urllib.request.Request(self.base + path, body, request_headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), response.headers

    def submit(self):
        with patch.object(self.app, 'ready', return_value=True):
            return self.request('/v1/sessions', self.data)

    def test_turn_pending_complete_and_missing(self):
        status, record, headers = self.submit()
        self.assertEqual(status, 202)
        url = headers['Location']
        self.assertEqual(url, record['url'])
        self.assertEqual(self.request(url)[1]['status'], 'Queued')
        self.assertEqual(self.request(url + '/result')[0], 202)
        self.assertEqual(self.request(url + '/turns/0')[0], 202)
        self.assertEqual(self.request(url + '/turns/-1')[0], 422)
        self.assertEqual(self.request(url + '/turns/abc')[0], 422)
        # Run the real session engine and inspect the HTTP boundary during generation.
        paths = list((self.root / '.runtime/queue').glob('*.json'))
        from chatwell.session import run
        observed = []
        def runner(config, characters, client, output_dir, observer, **kwargs):
            def observe(path):
                observer(path)
                session = read_json(path)
                if len(session['conversation']['turns']) == 1 and session['status'] == 'Running':
                    observed.append(self.request(url + '/turns/0')[0])
                    self.assertEqual(self.request(url + '/turns/1')[0], 202)
            return run(config, characters, client, output_dir, observer=observe, **kwargs)
        with patch.object(jobs, 'ROOT', self.root):
            jobs.process_job(paths[0], MockClient(), {}, runner=runner)
        self.assertTrue(observed)
        self.assertTrue(all(status == 200 for status in observed))
        status, turn, _ = self.request(url + '/turns/1')
        self.assertEqual(status, 200)
        self.assertEqual(set(turn), {'index', 'character', 'coordinator'})
        self.assertEqual(turn['index'], 1)
        self.assertIn('speech', turn['character']['output'])
        status, missing, _ = self.request(url + '/turns/3')
        self.assertEqual(status, 404)
        self.assertEqual(missing['status'], 'Ended')
        self.assertEqual(self.request(url + '/result')[0], 200)

    def test_no_auth_validation_and_missing(self):
        self.app.started.set()
        self.assertEqual(self.request('/healthz')[0], 200)
        self.app.started.clear()
        self.assertEqual(self.request('/healthz')[0], 503)
        self.assertEqual(self.request('/v1/sessions/' + 'a' * 32)[0], 404)
        self.assertEqual(self.request('/v1/sessions', raw=b'{')[0], 400)
        self.assertEqual(self.request('/v1/sessions', {})[0], 422)
        self.assertEqual(self.request('/v1/sessions', self.data)[0], 503)
        self.assertEqual(self.request('/v1/sessions', self.data,
                                     headers={'Content-Type': 'text/plain'})[0], 415)
        # The server rejects oversized bodies from headers without reading them.
        # Send headers alone so a client write cannot race the server's close.
        from http.client import HTTPConnection
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('POST', '/v1/sessions', headers={
                'Content-Type': 'application/json',
                'Content-Length': str(1024 * 1024 + 1)})
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()
        self.assertFalse(list((self.root / 'sessions').glob('*.json')))

    def test_failed_session_and_internal_details_are_private(self):
        _, record, _ = self.submit()
        path = next((self.root / 'sessions').glob('*.json'))
        session = read_json(path)
        session['status'] = 'Failed'
        session['conversation']['stop_reason'] = 'error: OSError: /secret/path'
        session['conversation']['ending'] = {'output': None, 'metrics': {}, 'error': '/secret/path'}
        session['conversation']['pending_turn'] = {'secret': '/secret/path'}
        save(path, session)
        status, missing, _ = self.request(record['url'] + '/turns/0')
        self.assertEqual(status, 404)
        self.assertEqual(missing['status'], 'Failed')
        result = self.request(record['url'] + '/result')[1]
        self.assertNotIn('/secret/path', json.dumps(result))
        self.assertNotIn('pending_turn', result['conversation'])

    def test_concurrent_submissions_create_distinct_sessions(self):
        from concurrent.futures import ThreadPoolExecutor
        original = jobs.ROOT
        with patch.object(self.app, 'ready', return_value=True):
            with ThreadPoolExecutor(max_workers=4) as pool:
                records = list(pool.map(lambda _: self.app.submit(self.data), range(4)))
        self.assertEqual(len({record['id'] for record in records}), 4)
        self.assertEqual(len(list((self.root / '.runtime/queue').glob('*.json'))), 4)
        self.assertEqual(jobs.ROOT, original)
