"""Single-process HTTP API with sequential, persistent conversation generation."""
import json
import logging
import signal
import threading
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import jobs, validation
from .client import reject_constant
from .locking import worker_lock
from .storage import read_json


class APIError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class Application:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.started = threading.Event()
        self.stopping = threading.Event()
        self.submission_lock = threading.Lock()

    def ready(self):
        return self.started.is_set() and not self.stopping.is_set()

    def session(self, identity):
        if not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise APIError(404, 'session_not_found')
        for path in (self.root / 'sessions').glob(f'*-{identity[:12]}.json'):
            record = read_json(path)
            if record['id'] == identity:
                return record
        raise APIError(404, 'session_not_found')

    @staticmethod
    def public_call(call):
        if call is None:
            return None
        return {key: call[key] for key in ('output', 'metrics', 'model') if key in call}

    @classmethod
    def public_turn(cls, turn):
        return {'index': turn['index'], 'character': cls.public_call(turn['character']),
                'coordinator': cls.public_call(turn['coordinator'])}

    @staticmethod
    def summary(record):
        return {key: record[key] for key in ('id', 'status', 'timestamps', 'progress')} | {
            'stop_reason': record['conversation']['stop_reason'].split(':', 1)[0],
            'ending_error': bool(record['conversation'].get('ending_error'))}

    def submit(self, data):
        validation.keys(data, ('config', 'characters'))
        validation.request(data['config'], data['characters'])
        with self.submission_lock:
            if not self.ready():
                raise APIError(503, 'not_ready')
            path = jobs.enqueue_data(data, directory=self.root)
            return read_json(read_json(path)['session_path'])


def make_server(host, port, app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log credentials, request bodies or URL parameters.

        def reply(self, status, body, headers=None):
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def handle_request(self):
            try:
                if app.stopping.is_set():
                    raise APIError(503, 'shutting_down')
                path = self.path
                if self.command == 'GET' and path == '/healthz':
                    ready = app.ready()
                    self.reply(200 if ready else 503, {'ok': ready})
                    return
                if self.command == 'POST' and path == '/v1/sessions':
                    if self.headers.get_content_type() != 'application/json':
                        raise APIError(415, 'expected_application_json')
                    if self.headers.get('Transfer-Encoding'):
                        raise APIError(400, 'unsupported_transfer_encoding')
                    try:
                        length = int(self.headers.get('Content-Length', '-1'))
                    except ValueError:
                        raise APIError(400, 'invalid_content_length')
                    if length < 0:
                        raise APIError(411, 'content_length_required')
                    if length > 1024 * 1024:
                        raise APIError(413, 'request_too_large')
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise APIError(400, 'incomplete_body')
                    try:
                        data = json.loads(raw.decode('utf-8'), parse_constant=reject_constant)
                    except (ValueError, UnicodeError, RecursionError):
                        raise APIError(400, 'invalid_json')
                    try:
                        record = app.submit(data)
                    except (ValueError, TypeError, OverflowError, RecursionError):
                        raise APIError(422, 'invalid_input')
                    url = '/v1/sessions/' + record['id']
                    self.reply(202, {**app.summary(record), 'url': url}, {'Location': url})
                    return
                match = re.fullmatch(r'/v1/sessions/([^/]+)(?:/(result|turns/([^/]+)))?', path)
                if self.command != 'GET' or not match:
                    raise APIError(404, 'not_found')
                identity, resource, index = match.groups()
                if index is not None and not re.fullmatch(r'0|[1-9][0-9]{0,8}', index):
                    raise APIError(422, 'invalid_turn_index')
                record = app.session(identity)
                terminal = record['status'] in ('Ended', 'Failed')
                summary = app.summary(record)
                if index is not None:
                    number = int(index)
                    turns = record['conversation']['turns']
                    if number < len(turns):
                        self.reply(200, app.public_turn(turns[number]))
                    elif terminal:
                        self.reply(404, {'error': 'turn_not_found', **summary})
                    else:
                        self.reply(202, summary, {'Retry-After': '1', 'Location': path})
                elif resource == 'result' and terminal:
                    # Exclude internal exception text, pending output and host metadata.
                    conversation = record['conversation']
                    self.reply(200, {**summary, 'conversation': {
                        'initialization': app.public_call(conversation.get('initialization')),
                        'turns': [app.public_turn(turn) for turn in conversation['turns']],
                        'ending': app.public_call(conversation.get('ending'))}})
                elif resource == 'result':
                    self.reply(202, summary, {'Retry-After': '1', 'Location': path})
                else:
                    self.reply(200, summary)
            except APIError as error:
                self.reply(error.status, {'error': error.code})
            except (TimeoutError, ConnectionError):
                self.close_connection = True
            except Exception:
                self.reply(500, {'error': 'internal_error'})

        do_GET = handle_request
        do_POST = handle_request

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

    return ThreadingHTTPServer((host, port), Handler)


def serve(args):
    from .settings import inference_settings, read_settings
    from .client import LlamaClient, MockClient

    configured = read_settings(args.settings)
    def option(name, key):
        value = getattr(args, name)
        return value if value is not None else configured[key]
    host = option('host', 'CHATWELL_HOST')
    port = int(option('port', 'CHATWELL_PORT'))
    if not 0 <= port <= 65535:
        raise ValueError('ポートが不正です。')
    # Relative data paths in .env are resolved beside that file.
    directory = (Path(args.data_dir).resolve() if args.data_dir else
                 Path(args.settings).resolve().parent / configured['CHATWELL_DATA_DIR'])
    client = MockClient() if args.mock else LlamaClient(**inference_settings(configured))
    app = Application(directory)
    jobs.configure(directory)

    def interrupt(signum, frame):
        app.stopping.set()
        # Let the first interrupt finish saving state even if Ctrl+C is repeated.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise KeyboardInterrupt

    # Generation stays on the main thread so Ctrl+C interrupts a blocking LLM call.
    # HTTP requests run in daemon threads and never outlive this process.
    with worker_lock(app.root / '.runtime' / 'worker.lock'):
        with make_server(host, port, app) as server:
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={'poll_interval': .1}, daemon=True)
            previous = signal.signal(signal.SIGINT, interrupt)
            try:
                jobs.recover()
                app.started.set()
                thread.start()
                logging.info('CHATWELL: http://%s:%s — Ctrl+Cで終了%s',
                             host, server.server_port, ' (MOCK)' if args.mock else '')
                jobs.work(client, {'mode': 'mock' if args.mock else 'model'},
                          should_stop=app.stopping.is_set, acquire_lock=False)
            except KeyboardInterrupt:
                pass
            finally:
                app.stopping.set()
                if thread.ident is not None:
                    server.shutdown()
                    thread.join()
                # An in-flight submission may finish saving; preserve it as Queued.
                with app.submission_lock:
                    jobs.recover()
                signal.signal(signal.SIGINT, previous)
                logging.info('CHATWELL: stopped')
