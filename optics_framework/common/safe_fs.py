"""Project file writes that never follow a symlink out of the intended tree."""
import os
import shutil
import stat
import tempfile
from pathlib import Path

from optics_framework.common.error import Code, OpticsError

_NEW_FILE_MODE = 0o644


def refuse_symlinks(path: str | os.PathLike, root: str | os.PathLike) -> Path:
    """Return ``path`` once it lies under ``root`` with no symlink at or below ``root``."""
    root_path = Path(os.path.abspath(root))
    target = Path(os.path.abspath(os.path.join(root_path, path)))
    if not target.is_relative_to(root_path):
        raise OpticsError(Code.E0501, f"Refusing to write {target}: it is outside {root_path}.")
    current = root_path
    for part in target.relative_to(root_path).parts:
        current = current / part
        if current.is_symlink():
            raise OpticsError(
                Code.E0501,
                f"Refusing to write through the symlink {current}. Replace it with a real directory or file.",
            )
    return target


def make_dirs(path: str | os.PathLike, root: str | os.PathLike) -> Path:
    """Create ``path`` under ``root``, refusing symlinked components."""
    target = refuse_symlinks(path, root)
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_text(path: str | os.PathLike, text: str, root: str | os.PathLike | None = None) -> Path:
    """Atomically replace ``path`` with ``text``; a symlink at ``path`` is replaced, never written through."""
    target = Path(path)
    if root is not None:
        target = refuse_symlinks(target.parent, root) / target.name
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, _mode_of(target))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def replace_tree(source: str | os.PathLike, destination: str | os.PathLike, root: str | os.PathLike) -> Path:
    """Replace the directory ``destination`` under ``root`` with a copy of ``source``."""
    target = refuse_symlinks(destination, root)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def _mode_of(path: Path) -> int:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _NEW_FILE_MODE
    return stat.S_IMODE(info.st_mode) if stat.S_ISREG(info.st_mode) else _NEW_FILE_MODE
