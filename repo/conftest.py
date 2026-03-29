from pathlib import Path

from _pytest import pathlib as pytest_pathlib


_original_rm_rf = pytest_pathlib.rm_rf


def _safe_rm_rf(path):
    try:
        _original_rm_rf(path)
    except PermissionError:
        try:
            resolved = Path(path).resolve()
        except Exception:
            return
        allowed_roots = {
            Path.cwd().resolve() / ".pytest_tmp",
            Path.cwd().resolve() / ".pytest_runtime",
        }
        if any(root == resolved or root in resolved.parents for root in allowed_roots):
            return
        raise


pytest_pathlib.rm_rf = _safe_rm_rf
