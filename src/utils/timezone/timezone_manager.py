"""
Timezone Manager

Centralized timezone handling for the trading engine.
All timezone-related logic should use this module.

Author: client Options Trading Engine
"""

from datetime import datetime, time as dt_time, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


class TimezoneManager:
    """
    Centralized timezone management for the trading engine.

    Handles:
    - Timezone definitions (US Eastern, IST)
    - Time conversions between timezones
    - Session time calculations
    - Market time utilities
    """

    # Standard timezone definitions
    US_EASTERN = ZoneInfo("America/New_York")
    IST = ZoneInfo("Asia/Kolkata")
    UTC = ZoneInfo("UTC")

    @classmethod
    def now_eastern(cls) -> datetime:
        """Get current datetime in US Eastern timezone."""
        return datetime.now(cls.US_EASTERN)

    @classmethod
    def now_ist(cls) -> datetime:
        """Get current datetime in IST timezone."""
        return datetime.now(cls.IST)

    @classmethod
    def now_utc(cls) -> datetime:
        """Get current datetime in UTC timezone."""
        return datetime.now(cls.UTC)

    @classmethod
    def convert_ist_to_eastern(cls, ist_time: dt_time) -> dt_time:
        """
        Convert a time in IST to US Eastern Time.

        The config file uses IST times (e.g., 20:00 IST = 9:30 AM EST).
        IBKR returns bar timestamps in US Eastern Time.
        This function converts config times to Eastern for proper comparison.

        Args:
            ist_time: Time in IST timezone

        Returns:
            Time in US Eastern timezone
        """
        # Create a datetime for today with the IST time
        today = datetime.now(cls.IST).date()
        ist_datetime = datetime.combine(today, ist_time, tzinfo=cls.IST)

        # Convert to US Eastern
        eastern_datetime = ist_datetime.astimezone(cls.US_EASTERN)

        return eastern_datetime.time()

    @classmethod
    def convert_eastern_to_ist(cls, eastern_time: dt_time) -> dt_time:
        """
        Convert a time in US Eastern to IST.

        Args:
            eastern_time: Time in US Eastern timezone

        Returns:
            Time in IST timezone
        """
        # Create a datetime for today with the Eastern time
        today = datetime.now(cls.US_EASTERN).date()
        eastern_datetime = datetime.combine(today, eastern_time, tzinfo=cls.US_EASTERN)

        # Convert to IST
        ist_datetime = eastern_datetime.astimezone(cls.IST)

        return ist_datetime.time()

    @classmethod
    def to_eastern(cls, dt: datetime) -> datetime:
        """
        Convert any datetime to US Eastern timezone.

        Args:
            dt: Datetime object (timezone-aware or naive)

        Returns:
            Datetime in US Eastern timezone
        """
        if dt.tzinfo is None:
            # Assume naive datetime is in Eastern
            return dt.replace(tzinfo=cls.US_EASTERN)
        return dt.astimezone(cls.US_EASTERN)

    @classmethod
    def to_ist(cls, dt: datetime) -> datetime:
        """
        Convert any datetime to IST timezone.

        Args:
            dt: Datetime object (timezone-aware or naive)

        Returns:
            Datetime in IST timezone
        """
        if dt.tzinfo is None:
            # Assume naive datetime is in Eastern
            dt = dt.replace(tzinfo=cls.US_EASTERN)
        return dt.astimezone(cls.IST)

    @classmethod
    def is_time_in_session(cls, current: dt_time, start: dt_time, end: dt_time) -> bool:
        """
        Check if current time is within session window.
        Handles overnight sessions where end < start.

        Args:
            current: Current time to check
            start: Session start time
            end: Session end time

        Returns:
            True if current time is within session
        """
        if start <= end:
            # Normal session (e.g., 09:30 to 16:00)
            return start <= current <= end
        else:
            # Overnight session (e.g., 20:00 to 01:30)
            return current >= start or current <= end

    @classmethod
    def is_session_ended(cls, current: dt_time, start: dt_time, end: dt_time) -> bool:
        """
        Check if session has ended.

        Args:
            current: Current time to check
            start: Session start time
            end: Session end time

        Returns:
            True if session has ended
        """
        if start <= end:
            # Normal session
            return current > end
        else:
            # Overnight session
            return end < current < start

    @classmethod
    def add_minutes_to_time(cls, t: dt_time, minutes: int) -> dt_time:
        """
        Add minutes to a time object.

        Args:
            t: Time object
            minutes: Minutes to add (can be negative)

        Returns:
            New time object with minutes added
        """
        dt = datetime.combine(datetime.today(), t)
        dt += timedelta(minutes=minutes)
        return dt.time()

    @classmethod
    def time_diff_minutes(cls, t1: dt_time, t2: dt_time) -> int:
        """
        Calculate difference in minutes between two times.

        Args:
            t1: First time
            t2: Second time

        Returns:
            Difference in minutes (t1 - t2)
        """
        dt1 = datetime.combine(datetime.today(), t1)
        dt2 = datetime.combine(datetime.today(), t2)
        diff = dt1 - dt2
        return int(diff.total_seconds() / 60)

    @classmethod
    def format_time_eastern(cls, dt: datetime, fmt: str = "%H:%M:%S") -> str:
        """
        Format datetime in Eastern timezone.

        Args:
            dt: Datetime to format
            fmt: Format string

        Returns:
            Formatted time string
        """
        eastern_dt = cls.to_eastern(dt)
        return eastern_dt.strftime(fmt)

    @classmethod
    def format_time_ist(cls, dt: datetime, fmt: str = "%H:%M:%S") -> str:
        """
        Format datetime in IST timezone.

        Args:
            dt: Datetime to format
            fmt: Format string

        Returns:
            Formatted time string
        """
        ist_dt = cls.to_ist(dt)
        return ist_dt.strftime(fmt)


# Convenience aliases for direct import
US_EASTERN = TimezoneManager.US_EASTERN
IST = TimezoneManager.IST
UTC = TimezoneManager.UTC
