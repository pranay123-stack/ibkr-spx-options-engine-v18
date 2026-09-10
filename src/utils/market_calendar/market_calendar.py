"""
US Stock Market Calendar
Handles market hours, holidays, and trading day calculations

TIMEZONE CONVENTION:
- All market times are in US Eastern Time (handles EST/EDT automatically)
- Config times may be in IST and should be converted when comparing
- Use US_EASTERN for consistent timezone handling across the codebase
"""

from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional, Tuple, Dict
from dataclasses import dataclass
from enum import Enum

from ..timezone import US_EASTERN, IST


class MarketStatus(Enum):
    OPEN = "OPEN"
    CLOSED_WEEKEND = "CLOSED_WEEKEND"
    CLOSED_HOLIDAY = "CLOSED_HOLIDAY"
    CLOSED_AFTER_HOURS = "CLOSED_AFTER_HOURS"
    CLOSED_BEFORE_HOURS = "CLOSED_BEFORE_HOURS"


@dataclass
class MarketInfo:
    """Information about current market status"""
    status: MarketStatus
    is_open: bool
    current_time_et: datetime
    current_time_ist: datetime
    holiday_name: Optional[str] = None
    next_open_et: Optional[datetime] = None
    next_open_ist: Optional[datetime] = None
    message: str = ""


# US Stock Market Holidays with names
# Format: {(month, day): "Holiday Name"} for fixed holidays
# Floating holidays are calculated dynamically
US_FIXED_HOLIDAYS = {
    (1, 1): "New Year's Day",
    (6, 19): "Juneteenth National Independence Day",
    (7, 4): "Independence Day",
    (12, 25): "Christmas Day",
}


def get_us_market_holidays(year: int) -> Dict[date, str]:
    """
    Get all US stock market holidays for a given year with their names.

    Returns:
        Dict mapping date to holiday name
    """
    holidays = {}

    # Fixed holidays
    for (month, day), name in US_FIXED_HOLIDAYS.items():
        holiday_date = date(year, month, day)
        # If holiday falls on weekend, it's observed on nearest weekday
        if holiday_date.weekday() == 5:  # Saturday -> Friday
            holiday_date = holiday_date - timedelta(days=1)
        elif holiday_date.weekday() == 6:  # Sunday -> Monday
            holiday_date = holiday_date + timedelta(days=1)
        holidays[holiday_date] = name

    # MLK Day (3rd Monday of January)
    jan1 = date(year, 1, 1)
    days_until_monday = (7 - jan1.weekday()) % 7
    first_monday = jan1 + timedelta(days=days_until_monday)
    if first_monday.month != 1:  # If Jan 1 is Monday
        first_monday = jan1
    mlk_day = first_monday + timedelta(weeks=2)
    holidays[mlk_day] = "Martin Luther King Jr. Day"

    # Presidents Day (3rd Monday of February)
    feb1 = date(year, 2, 1)
    days_until_monday = (7 - feb1.weekday()) % 7
    first_monday = feb1 + timedelta(days=days_until_monday)
    if first_monday.month != 2:
        first_monday = feb1
    presidents_day = first_monday + timedelta(weeks=2)
    holidays[presidents_day] = "Presidents Day"

    # Good Friday (Friday before Easter)
    # Using anonymous Gregorian algorithm for Easter
    easter = calculate_easter(year)
    good_friday = easter - timedelta(days=2)
    holidays[good_friday] = "Good Friday"

    # Memorial Day (Last Monday of May)
    may31 = date(year, 5, 31)
    days_since_monday = may31.weekday()
    memorial_day = may31 - timedelta(days=days_since_monday)
    holidays[memorial_day] = "Memorial Day"

    # Labor Day (1st Monday of September)
    sep1 = date(year, 9, 1)
    days_until_monday = (7 - sep1.weekday()) % 7
    labor_day = sep1 + timedelta(days=days_until_monday)
    holidays[labor_day] = "Labor Day"

    # Thanksgiving (4th Thursday of November)
    nov1 = date(year, 11, 1)
    days_until_thursday = (3 - nov1.weekday() + 7) % 7
    first_thursday = nov1 + timedelta(days=days_until_thursday)
    thanksgiving = first_thursday + timedelta(weeks=3)
    holidays[thanksgiving] = "Thanksgiving Day"

    return holidays


def calculate_easter(year: int) -> date:
    """
    Calculate Easter Sunday using the Anonymous Gregorian algorithm.
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_us_market_holiday(check_date: date) -> Tuple[bool, Optional[str]]:
    """
    Check if a date is a US market holiday.

    Returns:
        Tuple of (is_holiday, holiday_name)
    """
    holidays = get_us_market_holidays(check_date.year)
    if check_date in holidays:
        return True, holidays[check_date]
    return False, None


def get_next_trading_day(from_date: date) -> date:
    """
    Get the next trading day (skipping weekends and holidays).
    """
    next_day = from_date + timedelta(days=1)
    holidays = get_us_market_holidays(next_day.year)

    while next_day.weekday() >= 5 or next_day in holidays:
        next_day += timedelta(days=1)
        # Handle year boundary
        if next_day.year != from_date.year:
            holidays.update(get_us_market_holidays(next_day.year))

    return next_day


def get_market_status() -> MarketInfo:
    """
    Get comprehensive market status information.

    Returns:
        MarketInfo with current status and next open time
    """
    # Use zoneinfo for proper timezone handling
    now_et = datetime.now(US_EASTERN)
    now_ist = datetime.now(IST)

    today_et = now_et.date()
    current_time = now_et.time()

    # Market hours (Regular Trading Hours)
    market_open = dt_time(9, 30)
    market_close = dt_time(16, 0)  # 4:00 PM ET

    # Check if weekend
    if today_et.weekday() >= 5:
        days_until_monday = 7 - today_et.weekday()
        next_monday = today_et + timedelta(days=days_until_monday)

        # Check if Monday is a holiday
        holidays = get_us_market_holidays(next_monday.year)
        while next_monday in holidays or next_monday.weekday() >= 5:
            next_monday += timedelta(days=1)
            if next_monday.year != today_et.year:
                holidays.update(get_us_market_holidays(next_monday.year))

        next_open_et = datetime.combine(next_monday, market_open, tzinfo=US_EASTERN)
        next_open_ist = next_open_et.astimezone(IST)

        day_name = "Saturday" if today_et.weekday() == 5 else "Sunday"
        return MarketInfo(
            status=MarketStatus.CLOSED_WEEKEND,
            is_open=False,
            current_time_et=now_et,
            current_time_ist=now_ist,
            next_open_et=next_open_et,
            next_open_ist=next_open_ist,
            message=f"Today is {day_name} - US markets are CLOSED for the weekend."
        )

    # Check if holiday
    is_holiday, holiday_name = is_us_market_holiday(today_et)
    if is_holiday:
        next_trading = get_next_trading_day(today_et)
        next_open_et = datetime.combine(next_trading, market_open, tzinfo=US_EASTERN)
        next_open_ist = next_open_et.astimezone(IST)

        return MarketInfo(
            status=MarketStatus.CLOSED_HOLIDAY,
            is_open=False,
            current_time_et=now_et,
            current_time_ist=now_ist,
            holiday_name=holiday_name,
            next_open_et=next_open_et,
            next_open_ist=next_open_ist,
            message=f"Today ({today_et.strftime('%B %d, %Y')}) is {holiday_name} - US markets are CLOSED."
        )

    # Check market hours
    if current_time < market_open:
        next_open_et = datetime.combine(today_et, market_open, tzinfo=US_EASTERN)
        next_open_ist = next_open_et.astimezone(IST)

        return MarketInfo(
            status=MarketStatus.CLOSED_BEFORE_HOURS,
            is_open=False,
            current_time_et=now_et,
            current_time_ist=now_ist,
            next_open_et=next_open_et,
            next_open_ist=next_open_ist,
            message=f"Market is CLOSED (Pre-market). Opens at {market_open.strftime('%I:%M %p')} ET."
        )

    if current_time > market_close:
        next_trading = get_next_trading_day(today_et)
        next_open_et = datetime.combine(next_trading, market_open, tzinfo=US_EASTERN)
        next_open_ist = next_open_et.astimezone(IST)

        return MarketInfo(
            status=MarketStatus.CLOSED_AFTER_HOURS,
            is_open=False,
            current_time_et=now_et,
            current_time_ist=now_ist,
            next_open_et=next_open_et,
            next_open_ist=next_open_ist,
            message=f"Market is CLOSED (After-hours). Opens {next_trading.strftime('%A, %B %d')} at {market_open.strftime('%I:%M %p')} ET."
        )

    # Market is open
    return MarketInfo(
        status=MarketStatus.OPEN,
        is_open=True,
        current_time_et=now_et,
        current_time_ist=now_ist,
        message=f"Market is OPEN. Closes at {market_close.strftime('%I:%M %p')} ET."
    )


def print_market_status(logger=None):
    """Print formatted market status using logger."""
    import logging
    if logger is None:
        logger = logging.getLogger("MarketCalendar")

    info = get_market_status()

    logger.info("=" * 60)
    logger.info("US STOCK MARKET STATUS")
    logger.info("=" * 60)

    # Current times
    et_str = info.current_time_et.strftime("%Y-%m-%d %I:%M:%S %p") if hasattr(info.current_time_et, 'strftime') else str(info.current_time_et)
    ist_str = info.current_time_ist.strftime("%Y-%m-%d %I:%M:%S %p") if hasattr(info.current_time_ist, 'strftime') else str(info.current_time_ist)

    logger.info(f"  Current Time (ET):  {et_str}")
    logger.info(f"  Current Time (IST): {ist_str}")
    logger.info("-" * 60)

    # Status
    if info.is_open:
        logger.info(f"  Status: MARKET OPEN")
    else:
        logger.info(f"  Status: MARKET CLOSED")
        if info.holiday_name:
            logger.info(f"  Reason: {info.holiday_name}")

    logger.info(f"  {info.message}")

    # Next open time
    if info.next_open_et and not info.is_open:
        next_et_str = info.next_open_et.strftime("%A, %B %d, %Y at %I:%M %p ET")
        next_ist_str = info.next_open_ist.strftime("%A, %B %d, %Y at %I:%M %p IST")
        logger.info("-" * 60)
        logger.info(f"  Next Market Open:")
        logger.info(f"    US Eastern: {next_et_str}")
        logger.info(f"    India (IST): {next_ist_str}")

    logger.info("=" * 60)

    return info


def get_trading_day_expiry() -> Tuple[date, Optional[str]]:
    """
    Get the appropriate expiry date for options trading.
    Accounts for weekends and holidays.

    Returns:
        Tuple of (expiry_date, message if not today)
    """
    now_et = datetime.now(US_EASTERN)
    today = now_et.date()

    # Check if weekend
    if today.weekday() >= 5:
        next_trading = get_next_trading_day(today - timedelta(days=1))
        return next_trading, f"Weekend - using next trading day: {next_trading}"

    # Check if holiday
    is_holiday, holiday_name = is_us_market_holiday(today)
    if is_holiday:
        next_trading = get_next_trading_day(today)
        return next_trading, f"{holiday_name} - using next trading day: {next_trading}"

    return today, None


if __name__ == "__main__":
    # Test the module with logging
    import logging
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
    test_logger = logging.getLogger("MarketCalendarTest")

    print_market_status(test_logger)

    # Show holidays for current year
    from datetime import date
    year = date.today().year
    test_logger.info(f"US Market Holidays for {year}:")
    test_logger.info("-" * 40)
    holidays = get_us_market_holidays(year)
    for d in sorted(holidays.keys()):
        test_logger.info(f"  {d.strftime('%B %d, %Y'):25} - {holidays[d]}")
