"""One experiment per target; the lock file records which run owns the fault."""
import os

from .config import runs_dir


def _path(target: str):
    d = runs_dir() / "locks"
    d.mkdir(exist_ok=True)
    return d / f"{target}.lock"


def acquire(target: str, run_id: str) -> bool:
    try:
        fd = os.open(_path(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(run_id)
    return True


def owner(target: str):
    try:
        return _path(target).read_text().strip()
    except FileNotFoundError:
        return None


def release(target: str, run_id: str) -> bool:
    if owner(target) != run_id:
        return False
    _path(target).unlink(missing_ok=True)
    return True
