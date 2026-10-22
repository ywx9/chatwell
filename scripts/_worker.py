"""Internal worker entry point, launched by start.py."""
import argparse
import logging
from _jobs import work, read_json

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    work(read_json(args.settings))
