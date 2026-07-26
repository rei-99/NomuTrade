"""UTC timestamp helpers. All persisted timestamps are timezone-aware UTC."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to aware UTC.

    SQLite round-trips DateTime(timezone=True) values as naive; treat naive
    values as UTC rather than raising on comparison.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
