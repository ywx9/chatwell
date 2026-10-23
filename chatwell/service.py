"""Detached supervisors. Only launched children are owned; no PID-based killing."""
import argparse
import logging
import os
from pathlib import Path
import uuid
from .settings import load
from .client import LlamaClient, MockClient
from . import jobs
from .managed import supervise_server, ensure_server, attach_provenance
from .storage import read_json, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('server', 'worker'))
    parser.add_argument('--settings', required=True)
    parser.add_argument('--data-dir')
    parser.add_argument('--mock', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    settings = load(args)
    if args.mode == 'server':
        supervise_server(args.settings, settings)
        return
    if not args.mock:
        ensure_server(args.settings, settings)
    client = MockClient() if args.mock else LlamaClient(**settings)
    if not args.mock:
        attach_provenance(client, args.settings)
    directory = Path(args.data_dir) / '.runtime'
    instance = uuid.uuid4().hex
    def ready():
        from .tooling import worker_signature
        save(directory / 'worker.json', {'instance': instance, 'pid': os.getpid(), 'signature': worker_signature(args.settings, args.mock)})
    def should_stop():
        try:
            return read_json(directory / 'worker-stop.json').get('instance') == instance
        except (OSError, ValueError):
            return False
    jobs.configure(args.data_dir)
    # Persist no connection URL or credential values in session/job provenance.
    jobs.work(client, {'mode': 'mock' if args.mock else 'model'}, ready=ready, should_stop=should_stop)


if __name__ == '__main__':
    main()
