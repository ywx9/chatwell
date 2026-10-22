import argparse
import logging
import os

from .client import LocalClient, MockClient
from .session import run
from .storage import read_json
from .speakers import DEFAULT_MAX_TURNS, RECOMMENDATION_THRESHOLD


def main():
    parser = argparse.ArgumentParser(description='CHATWELL local multi-turn prototype')
    parser.add_argument('--config', default='examples/config.json')
    parser.add_argument('--characters', nargs='+', default=['examples/alice.json', 'examples/bob.json'])
    parser.add_argument('--output-dir', default='sessions')
    parser.add_argument('--endpoint', default=os.environ.get('CHATWELL_ENDPOINT', 'http://127.0.0.1:8080'))
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--max-turns', type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument('--recommendation-threshold', type=float, default=RECOMMENDATION_THRESHOLD)
    parser.add_argument('--debug-dir')
    parser.add_argument('--mock', action='store_true', help='Japanese fixture only; no inference')
    parser.add_argument('--probe', action='store_true', help='Test plain English and schema output before session')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        client = MockClient() if args.mock else LocalClient(args.endpoint, args.timeout, args.debug_dir)
        if args.mock:
            logging.warning('MOCK: fixed Japanese fixture, not model output')
        if args.probe:
            logging.info('English probe: %s', client.complete([{'role': 'user', 'content': 'Say hello in one short English sentence.'}], {'max_tokens': 64}))
        path = run(read_json(args.config), [read_json(p) for p in args.characters], client, args.output_dir,
                   max_turns=args.max_turns, recommendation_threshold=args.recommendation_threshold)
        print(path)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        logging.error('%s: %s', type(error).__name__, error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
