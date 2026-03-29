from pathlib import Path


Path(".pytest_runtime/tmp").mkdir(parents=True, exist_ok=True)
Path(".pytest_runtime/cache").mkdir(parents=True, exist_ok=True)

try:
    from _pytest import pathlib as pytest_pathlib  # type: ignore
except Exception:  # pragma: no cover
    pytest_pathlib = None

if pytest_pathlib is not None:
    _original_rm_rf = pytest_pathlib.rm_rf
    _original_cleanup_dead_symlinks = getattr(pytest_pathlib, "cleanup_dead_symlinks", None)

    def _safe_rm_rf(path):
        try:
            _original_rm_rf(path)
        except PermissionError:
            resolved = Path(path).resolve()
            allowed = {
                (Path.cwd() / ".pytest_tmp").resolve(),
                (Path.cwd() / ".pytest_runtime").resolve(),
                (Path.cwd() / ".pytest_runtime/tmp").resolve(),
            }
            if any(root == resolved or root in resolved.parents for root in allowed):
                return
            raise

    pytest_pathlib.rm_rf = _safe_rm_rf

    if _original_cleanup_dead_symlinks is not None:

        def _safe_cleanup_dead_symlinks(root, prefix):
            try:
                return _original_cleanup_dead_symlinks(root, prefix)  # type: ignore[misc]
            except PermissionError:
                resolved = Path(root).resolve()
                allowed = {
                    (Path.cwd() / ".pytest_runtime").resolve(),
                    (Path.cwd() / ".pytest_runtime/tmp").resolve(),
                    (Path.cwd() / ".pytest_tmp").resolve(),
                }
                if any(base == resolved or base in resolved.parents for base in allowed):
                    return
                raise

        pytest_pathlib.cleanup_dead_symlinks = _safe_cleanup_dead_symlinks
