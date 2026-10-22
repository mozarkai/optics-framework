import sys
from rich.console import Console
class _StdoutReplacingUnencodable:
    @property
    def encoding(self) -> str:
        return getattr(sys.stdout, "encoding", None) or "utf-8"
    def write(self, text: str) -> int:
        if sys.stdout is None:
            return len(text)
        return sys.stdout.write(text.encode(self.encoding, "replace").decode(self.encoding))
    def flush(self) -> None:
        if sys.stdout is not None:
            sys.stdout.flush()
    def isatty(self) -> bool:
        return sys.stdout is not None and sys.stdout.isatty()
    def fileno(self) -> int:
        if sys.stdout is None:
            raise OSError("sys.stdout is not set")
        return sys.stdout.fileno()
def safe_console() -> Console:
    return Console(file=_StdoutReplacingUnencodable())  # type: ignore[arg-type]
def can_encode(console: Console, text: str) -> bool:
    try:
        text.encode(console.encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True
