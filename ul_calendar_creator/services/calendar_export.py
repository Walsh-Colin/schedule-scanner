from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

try:
    from ..models import ClassEntry, TimetableExtraction
    from .teaching_weeks import compress_weeks
except ImportError:
    from models import ClassEntry, TimetableExtraction
    from services.teaching_weeks import compress_weeks


BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_FILE = BASE_DIR / "output" / "classes.ics"
TIMEZONE = "Europe/Dublin"
END_EARLY_MINUTES = 10

DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
DAY_INDEX = {day: index for index, day in enumerate(DAYS)}


def module_text(cls: ClassEntry) -> str:
    return cls.module_code.strip().upper()


def event_title(cls: ClassEntry) -> str:
    return (
        f"{cls.module_code.strip().upper()}"
        f" - {cls.class_type.strip()}"
        f" - {cls.lecturer.strip()}"
    )


def adjusted_end_time(end_time: str) -> str:
    original = datetime.strptime(end_time, "%H:%M")
    adjusted = original - timedelta(minutes=END_EARLY_MINUTES)
    return adjusted.strftime("%H:%M")


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def class_date(commencing_monday: date, day_name: str) -> date:
    return commencing_monday + timedelta(days=DAY_INDEX[day_name])


def make_event(cls: ClassEntry, week_lookup: dict[int, date]) -> Event:
    tz = ZoneInfo(TIMEZONE)
    occurrence_dates = sorted(
        class_date(week_lookup[week], cls.day)
        for week in cls.weeks
    )
    first_date = occurrence_dates[0]
    last_date = occurrence_dates[-1]
    start_clock = parse_hhmm(cls.start_time)
    timetable_end_clock = parse_hhmm(cls.end_time)
    start_dt = datetime.combine(first_date, start_clock, tzinfo=tz)
    timetable_end_dt = datetime.combine(
        first_date,
        timetable_end_clock,
        tzinfo=tz,
    )
    end_dt = timetable_end_dt - timedelta(minutes=END_EARLY_MINUTES)

    if end_dt <= start_dt:
        raise RuntimeError(
            f"Adjusted end time for {cls.module_code} "
            f"is not after its start time."
        )

    event = Event()
    uid_source = (
        f"{cls.module_code}|"
        f"{cls.day}|"
        f"{cls.start_time}|"
        f"{cls.end_time}|"
        f"{cls.class_type}|"
        f"{cls.lecturer}|"
        f"{cls.room}"
    )
    event.add(
        "uid",
        f"{uuid.uuid5(uuid.NAMESPACE_URL, uid_source)}@ul-calendar",
    )
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("summary", event_title(cls))
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)
    event.add("location", cls.room.strip())

    module_name = (
        cls.module_name.strip()
        if cls.module_name.strip()
        else "Not set (can be added in the UI)"
    )
    description = (
        f"Module code: {cls.module_code.strip().upper()}\n"
        f"Module name: {module_name}\n"
        f"Class type: {cls.class_type.strip()}\n"
        f"Professor: {cls.lecturer.strip()}\n"
        f"Room: {cls.room.strip()}\n"
        f"Teaching weeks: {compress_weeks(cls.weeks)}\n"
        f"Timetable time: {cls.start_time}-{cls.end_time}\n"
        f"Calendar time: {cls.start_time}-{end_dt.strftime('%H:%M')}"
    )
    event.add("description", description)

    until_local = datetime.combine(last_date, end_dt.time(), tzinfo=tz)
    event.add(
        "rrule",
        {
            "freq": "weekly",
            "until": until_local.astimezone(timezone.utc),
        },
    )

    valid_dates = set(occurrence_dates)
    current_date = first_date
    while current_date <= last_date:
        if current_date not in valid_dates:
            event.add(
                "exdate",
                datetime.combine(current_date, start_clock, tzinfo=tz),
            )
        current_date += timedelta(days=7)

    return event


def create_ics(
    data: TimetableExtraction,
    output_file: Path | None = None,
) -> Path:
    output_file = Path(output_file or OUTPUT_FILE)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    week_lookup = {
        row.week: row.as_date()
        for row in data.teaching_weeks
    }

    calendar = Calendar()
    calendar.add("prodid", "-//UL Calendar Creator//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", "UL Timetable")
    calendar.add("x-wr-timezone", TIMEZONE)

    classes = sorted(
        data.classes,
        key=lambda cls: (
            DAY_INDEX[cls.day],
            cls.start_time,
            cls.end_time,
        ),
    )
    for cls in classes:
        calendar.add_component(make_event(cls, week_lookup))

    output_file.write_bytes(calendar.to_ical())
    return output_file
