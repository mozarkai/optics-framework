import os


def optics_home() -> str:
    """The per-user optics folder: ``OPTICS_HOME`` (for a read-only HOME, containers or CI), else ``~/.optics``."""
    override = os.environ.get("OPTICS_HOME")
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), ".optics")
