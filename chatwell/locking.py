"""OS-backed locks are released automatically when a worker exits."""
from contextlib import contextmanager
import os
import time
from contextlib import ExitStack


@contextmanager
def worker_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('同じデータディレクトリのworkerが既に動作しています。') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def wait_lock(path, timeout=30):
    """Serialize launches/submissions without mistaking them for duplicate workers."""
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(worker_lock(path))
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('別の起動・登録操作の完了を確認できませんでした。再実行してください。') from None
                time.sleep(.1)
        yield
