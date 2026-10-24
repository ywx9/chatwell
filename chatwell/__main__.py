"""Start the API and process its persistent queue until Ctrl+C."""
import argparse
import logging
from pathlib import Path
from . import __version__


def parser():
    root = argparse.ArgumentParser(description='CHATWELL HTTP API')
    root.add_argument('--version', action='version', version=f'CHATWELL {__version__}')
    root.add_argument('--settings', default=str(Path(__file__).resolve().parents[1] / '.env'))
    root.add_argument('--data-dir')
    root.add_argument('--host')
    root.add_argument('--port', type=int)
    root.add_argument('--mock', action='store_true', help='Development only: fixed responses, no LLM')
    return root


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        from .api import serve
        serve(parser().parse_args(argv))
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        logging.error('%s: %s', type(error).__name__, error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
