import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chatwell.api import Application, make_server
from chatwell.storage import read_json

ROOT = Path(__file__).resolve().parents[1]


class EnqueueToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Application(self.root)
        self.app.started.set()
        self.server = make_server('127.0.0.1', 0, self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def close():
            self.server.shutdown()
            self.server.server_close()
            thread.join()
        self.addCleanup(close)
        self.settings = self.root / '.env'
        self.settings.write_text(f'CHATWELL_HOST=0.0.0.0\nCHATWELL_PORT={self.server.server_port}\n', encoding='utf-8')
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(('CHATWELL_', 'LLM_'))}
        self.environment['PYTHONUTF8'] = '1'

    def invoke(self, path):
        return subprocess.run([sys.executable, str(ROOT / 'tools/enqueue.py'),
                               '--input', str(path), '--settings', str(self.settings)],
                              cwd=self.root, env=self.environment, capture_output=True,
                              text=True, encoding='utf-8', timeout=5)

    def test_submits_file_contents_using_settings_from_another_directory(self):
        result = self.invoke(ROOT / 'samples/input.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record['status'], 'Queued')
        self.assertEqual(record['url'], '/v1/sessions/' + record['id'])
        job = read_json(next((self.root / '.runtime/queue').glob('*.json')))
        self.assertEqual(job['input'], read_json(ROOT / 'samples/input.json'))
        self.assertEqual(len(list((self.root / 'sessions').glob('*.json'))), 1)

    def test_invalid_input_and_api_rejection_do_not_enqueue(self):
        source = self.root / 'input.json'
        source.write_text('{', encoding='utf-8')
        self.assertEqual(self.invoke(source).returncode, 1)
        source.write_text('{}', encoding='utf-8')
        result = self.invoke(source)
        self.assertEqual(result.returncode, 1)
        self.assertIn('HTTP 422', result.stderr)
        self.assertEqual(self.invoke(self.root / 'missing.json').returncode, 1)
        self.assertFalse(list((self.root / '.runtime/queue').glob('*.json')))
