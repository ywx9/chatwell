"""Package historical validation evidence without changing local originals."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def redact(data):
    text = data.decode('utf-8-sig')
    for location, replacement in ((ROOT, '<WORKSPACE>'), (Path.home(), '<USER_HOME>')):
        variants = (str(location).replace('\\', '\\\\'), str(location), location.as_posix())
        for variant in variants:
            text = re.sub(re.escape(variant), lambda _: replacement, text, flags=re.IGNORECASE)
    # Catch other Windows user profiles that could occur in historical logs.
    text = re.sub(r'C:(?:\\\\|\\|/)Users(?:\\\\|\\|/)[^\\/\s"\']+',
                  '<USER_HOME>', text, flags=re.IGNORECASE)
    return text.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-log', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / '.local-artifacts/validation-initial.zip')
    args = parser.parse_args()
    if git('status', '--porcelain'):
        raise SystemExit('Commit source changes before packaging validation evidence.')
    if not args.test_log.is_file():
        raise SystemExit('The supplied test log does not exist.')
    commit = git('rev-parse', 'HEAD')
    files = []
    for directory in ('sessions', 'debug', '.runtime/logs'):
        files.extend(p for p in (ROOT / directory).rglob('*') if p.is_file())
    files.extend((ROOT / 'docs/validation').glob('*.md'))
    entries = [(p, p.relative_to(ROOT).as_posix().replace('.runtime/logs/', 'server-logs/')) for p in sorted(files)]
    entries.append((args.test_log, 'unit-tests.txt'))
    if not any(name.startswith('sessions/') for _, name in entries):
        raise SystemExit('No historical session files found.')
    manifest = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_commit': commit,
        'provenance': 'Historical model outputs predate the initial commit and span earlier implementation stages. They were not regenerated from source_commit. The unit test log is a separate current validation.',
        'model': 'gemma-4-E2B-it-Q4_K_M.gguf',
        'engine': 'llama.cpp b11342 / f1cee9941',
        'historical_commands': [
            'python -m chatwell --probe --timeout 240 --debug-dir debug/real-verified --output-dir sessions/real-verified',
            'python -m chatwell --max-turns 4 --timeout 240 --debug-dir debug/multiturn-verified --output-dir sessions/multiturn-verified',
        ],
        'current_test_command': 'python -m unittest discover -s tests -v',
        'redaction': 'UTF-8 BOM removed; local workspace/user profile paths replaced by <WORKSPACE>/<USER_HOME>. Originals unchanged; all other contents retained.',
        'files': [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in entries:
            original = path.read_bytes()
            packaged = redact(original)
            if path.suffix == '.json':
                json.loads(packaged)
            archive.writestr(name, packaged)
            manifest['files'].append({
                'path': name,
                'original_sha256': hashlib.sha256(original).hexdigest(),
                'packaged_sha256': hashlib.sha256(packaged).hexdigest(),
                'bytes': len(packaged),
            })
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    checksum = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(
        f'{checksum}  {args.output.name}\n', encoding='utf-8')
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP integrity check failed')
        for item in manifest['files']:
            if hashlib.sha256(archive.read(item['path'])).hexdigest() != item['packaged_sha256']:
                raise RuntimeError('ZIP member checksum mismatch')
    print(f'{args.output}: {len(entries)} evidence files, source {commit}')


if __name__ == '__main__':
    main()
