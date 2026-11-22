"""Shared errors/time for imported adapters and the command-line entry point."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re


def now():
    return datetime.now(timezone.utc).isoformat()


class FetchError(Exception):
    def __init__(self, code, retry_after=None, diagnostics=None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after
        self.diagnostics = diagnostics or {}


def http_failure(status, getheader, github=False, observed_at=None):
    """Classify only allowlisted header evidence; never retain bodies or raw headers."""
    observed_at = observed_at or datetime.now(timezone.utc)
    retry = getheader('Retry-After')
    reset = getheader('X-RateLimit-Reset')
    remaining = getheader('X-RateLimit-Remaining')
    def number(value):
        return int(value) if isinstance(value, str) and re.fullmatch(r'[0-9]{1,12}', value) else None
    retry_seconds = number(retry)
    retry_at = None
    retry_state = 'ABSENT' if retry is None else 'INVALID'
    if retry_seconds is not None:
        try:
            retry_at = datetime.fromtimestamp(observed_at.timestamp() + retry_seconds, timezone.utc)
            retry_state = 'VALID'
        except (ValueError, OverflowError, OSError):
            pass
    elif isinstance(retry, str) and len(retry) <= 80:
        try:
            parsed = parsedate_to_datetime(retry)
            if parsed.tzinfo is None:
                raise ValueError('Retry date requires a timezone')
            retry_at = parsed.astimezone(timezone.utc)
            retry_state = 'VALID'
        except (ValueError, TypeError, OverflowError):
            pass
    reset_at = None
    reset_state = 'ABSENT' if reset is None else 'INVALID'
    reset_seconds = number(reset)
    if reset_seconds is not None:
        try:
            reset_at = datetime.fromtimestamp(reset_seconds, timezone.utc)
            reset_state = 'VALID'
        except (ValueError, OverflowError, OSError):
            pass
    remaining_number = number(remaining)
    limited = status == 429 or (status == 403 and
              (remaining_number == 0 or retry_state == 'VALID'))
    code = ('RATE_LIMITED' if limited else
            {401:'AUTH_REQUIRED', 403:'ACCESS_DENIED', 402:'PAYMENT_REQUIRED_BLOCKED'}.get(status, 'HTTP_'+str(status)))
    times = [t for t in (retry_at, reset_at) if t is not None] if limited else []
    earliest = max([observed_at] + times).isoformat() if times else None
    diagnostics = {'http_status':status, 'classification':code,
                   'rate_limit_kind':('PRIMARY' if github and limited and remaining_number == 0
                                      else 'UNDETERMINED' if limited else 'NOT_ESTABLISHED'),
                   'remaining_state':('ABSENT' if remaining is None else
                                      'VALID' if remaining_number is not None else 'INVALID'),
                   'rate_limit_remaining':remaining_number,
                   'retry_after_state':retry_state,
                   'retry_after_seconds':retry_seconds if retry_state == 'VALID' else None,
                   'retry_after_at':retry_at.isoformat() if retry_at else None,
                   'reset_state':reset_state,
                   'rate_limit_reset_at':reset_at.isoformat() if reset_at else None,
                   'earliest_retry_at':earliest,
                   'retry_time_status':'HEADER_BASED' if earliest else 'UNKNOWN',
                   'cooldown_note':'Header-based lower bound; not a guarantee of access. No automatic retry.'}
    return FetchError(code, earliest, diagnostics)


def error_record(error, source, operation):
    return {'source':source, 'operation':operation, 'code':error.code,
            'retry_after':error.retry_after, **error.diagnostics}


def coverage_status(status):
    return 'COMPLETE' if status == 'FETCHED' else 'PARTIAL'
