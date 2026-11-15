"""Shared errors/time for imported adapters and the command-line entry point."""
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class FetchError(Exception):
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after
