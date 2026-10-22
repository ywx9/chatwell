"""Install a pinned official CPU build inside this workspace only."""
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

TAG = 'b11342'
URL = f'https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/llama-{TAG}-bin-win-cpu-x64.zip'
SHA256 = 'cc6f3ac938988f6ed4ce204b119d6d1ffe59342cb3157adef20e8132681acd8d'
target = Path(__file__).resolve().parents[1] / '.runtime' / TAG
if (target / 'llama-server.exe').exists():
    print(target)
else:
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('Release SHA256 mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            resolved = (target / entry.filename).resolve()
            if not resolved.is_relative_to(target.resolve()):
                raise RuntimeError('Unsafe archive path')
        archive.extractall(target)
    print(target)
