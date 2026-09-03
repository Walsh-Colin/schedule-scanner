#!/usr/bin/env python3

"""
UL Calendar Creator

Reads two screenshots from ./input:

1. Weekly timetable
2. Teaching-week / week-commencing table

Uses OpenAI vision to detect every visible scheduled class.

For each detected module code, OpenAI web search is used to find the
official University of Limerick module name.

Creates:

    output/classes.ics

Calendar event titles:

    MODULE CODE - MODULE NAME - CLASS TYPE - PROFESSOR

Example:

    CS4297 - Module Name - LAB - 2A - Andrew Ju

Each event also includes:

    Module code
    Module name
    Class type
    Professor
    Room
    Teaching weeks
    Original timetable time
    Adjusted calendar time

Classes finish 10 minutes before the timetable end time.

No Google Calendar API is used.

Required packages:

    python -m pip install --upgrade openai pydantic icalendar python-dotenv

Create a .env file beside this script:

    OPENAI_API_KEY=your-api-key-here

Run:

    python timetable_to_calender.py
"""

from __future__ import annotations

import base64
import mimetypes
import os
import sys
import uuid

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from icalendar import Calendar, Event
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator


BASE_DIR = Path(__file__).resolve().parent

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "classes.ics"
ENV_FILE = BASE_DIR / ".env"

OPENAI_MODEL = "gpt-5.6"

SEMESTER_YEAR = 2026
TIMEZONE = "Europe/Dublin"

MAX_EXTRACTION_ATTEMPTS = 3
END_EARLY_MINUTES = 10

load_dotenv(ENV_FILE)


DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

DAY_INDEX = {
    day: index
    for index, day in enumerate(DAYS)
}


class TeachingWeek(BaseModel):
    week: int = Field(ge=1, le=60)
    day: int = Field(ge=1, le=31)
    month: int = Field(ge=1, le=12)

    def as_date(self) -> date:
        result = date(
            SEMESTER_YEAR,
            self.month,
            self.day,
        )

        if result.weekday() != 0:
            raise ValueError(
                f"Teaching week {self.week} does not begin "
                f"on a Monday: {result}"
            )

        return result


class ClassEntry(BaseModel):
    day: Literal[
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    start_time: str
    end_time: str

    module_code: str
    module_name: str

    class_type: str
    lecturer: str
    room: str

    weeks: list[int] = Field(min_length=1)

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        value = value.strip()

        datetime.strptime(
            value,
            "%H:%M",
        )

        return value

    @field_validator("weeks")
    @classmethod
    def validate_weeks(cls, value: list[int]) -> list[int]:
        clean = sorted(set(value))

        if not clean:
            raise ValueError(
                "At least one teaching week is required."
            )

        if min(clean) < 1 or max(clean) > 60:
            raise ValueError(
                "Invalid teaching week number."
            )

        return clean


class TimetableExtraction(BaseModel):
    teaching_weeks: list[TeachingWeek]
    classes: list[ClassEntry]


def find_input_images() -> list[Path]:
    if not INPUT_DIR.exists():
        raise FileNotFoundError(
            f"Input folder does not exist:\n{INPUT_DIR}"
        )

    extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
    }

    images = sorted(
        path
        for path in INPUT_DIR.iterdir()
        if (
            path.is_file()
            and path.suffix.lower() in extensions
        )
    )

    if len(images) != 2:
        raise RuntimeError(
            f"Expected exactly 2 images in input/. "
            f"Found {len(images)}."
        )

    return images


def image_as_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)

    if mime not in {
        "image/png",
        "image/jpeg",
        "image/webp",
    }:
        mime = "image/png"

    encoded = base64.b64encode(
        path.read_bytes()
    ).decode("utf-8")

    return f"data:{mime};base64,{encoded}"


SYSTEM_PROMPT = """
You are reading two University of Limerick timetable screenshots.

One image is a weekly timetable grid.

The other image contains teaching-week numbers and week-commencing dates.

Interpret the timetable VISUALLY.

For the weekly timetable:

- Return every genuine visible scheduled class.
- There is no predetermined number of classes.
- Each visible timetable block is one separate scheduled class.
- Never merge adjacent timetable blocks.
- Never duplicate a class.
- Classes touching at a time boundary are separate classes.
- Lecturer, room, module code, class type and teaching weeks must come
  from the same timetable block.
- Inspect the entire timetable from top to bottom.
- Inspect every visible day column.
- Do not invent classes.
- Carefully distinguish module codes beginning with CS.
- Preserve lecturer names accurately.
- Preserve room names accurately.
- Preserve class types accurately.

For module names:

- First read the module code from the timetable.
- Then use web search to find the official module name.
- Search specifically for the University of Limerick module.
- Prefer official University of Limerick websites and documentation.
- Search using the exact module code.
- Examples:
    University of Limerick CS4297
    UL CS4297 module
    site:ul.ie CS4297
- The module code must match exactly.
- Never use the title of a similar module with a different code.
- Never guess a module name.
- If the official module name cannot be reliably verified,
  return an empty string for module_name.

For the teaching-week image:

- Return every visible teaching-week row.
- Return the teaching week number.
- Return the commencing day as an integer.
- Return the commencing month as an integer.
- Do not generate a year.
"""


def build_user_prompt(
    attempt: int,
    previous_issues: list[str] | None,
) -> str:
    retry = ""

    if previous_issues:
        retry += "\nPrevious validation problems:\n"

        for issue in previous_issues:
            retry += f"- {issue}\n"

    return f"""
Read both attached screenshots carefully.

For EVERY genuine scheduled class return:

- day
- start_time in HH:MM 24-hour format
- end_time in HH:MM 24-hour format
- module_code
- module_name
- class_type
- lecturer
- room
- weeks expanded into a list of integers

Examples:

Wks:1-12

becomes:

[1,2,3,4,5,6,7,8,9,10,11,12]

Wks:4-12

becomes:

[4,5,6,7,8,9,10,11,12]


MODULE NAMES

After identifying the module codes from the screenshot:

1. Use web search to look up each module code.
2. Find its official University of Limerick module name.
3. Prefer official University of Limerick sources.
4. Match the exact module code.
5. Never guess.
6. If the module name cannot be verified, use "".


TEACHING WEEKS

For every teaching-week row return:

- week
- day
- month

Examples:

07/09

becomes:

day = 7
month = 9

05/10

becomes:

day = 5
month = 10


IMPORTANT

- There is no fixed number of classes.
- Return every real visible class.
- Do not invent classes.
- Do not merge neighbouring classes.
- Do not duplicate classes.
- Use the times printed inside each timetable block.
- Inspect the entire timetable.
- Keep lecturer and room information attached to the correct class.
- Search online only for module names.
- Timetable details must come from the screenshots.

Extraction attempt: {attempt}

{retry}
"""


def validate_extraction(
    data: TimetableExtraction,
) -> list[str]:
    issues: list[str] = []

    if not data.classes:
        issues.append(
            "No classes were extracted."
        )

    if not data.teaching_weeks:
        issues.append(
            "No teaching weeks were extracted."
        )

    week_numbers = [
        row.week
        for row in data.teaching_weeks
    ]

    if len(week_numbers) != len(set(week_numbers)):
        issues.append(
            "Duplicate teaching-week numbers detected."
        )

    week_lookup: dict[int, date] = {}

    for row in data.teaching_weeks:
        try:
            week_lookup[row.week] = row.as_date()

        except ValueError as exc:
            issues.append(str(exc))

    ordered_weeks = sorted(
        data.teaching_weeks,
        key=lambda row: row.week,
    )

    for previous, current in zip(
        ordered_weeks,
        ordered_weeks[1:],
    ):
        try:
            previous_date = previous.as_date()
            current_date = current.as_date()

        except ValueError:
            continue

        if current_date <= previous_date:
            issues.append(
                f"Teaching week {current.week} has a date "
                f"that is not after week {previous.week}."
            )

    seen = set()

    for index, cls in enumerate(
        data.classes,
        start=1,
    ):
        start = datetime.strptime(
            cls.start_time,
            "%H:%M",
        )

        end = datetime.strptime(
            cls.end_time,
            "%H:%M",
        )

        if end <= start:
            issues.append(
                f"Class {index} ({cls.module_code}) "
                f"has an invalid time range."
            )

        if not cls.module_code.strip():
            issues.append(
                f"Class {index} has no module code."
            )

        if not cls.class_type.strip():
            issues.append(
                f"{cls.module_code} has no class type."
            )

        if not cls.lecturer.strip():
            issues.append(
                f"{cls.module_code} has no professor."
            )

        if not cls.room.strip():
            issues.append(
                f"{cls.module_code} has no room."
            )

        missing_weeks = [
            week
            for week in cls.weeks
            if week not in week_lookup
        ]

        if missing_weeks:
            issues.append(
                f"{cls.module_code} references teaching "
                f"weeks without dates: {missing_weeks}"
            )

        key = (
            cls.day,
            cls.start_time,
            cls.end_time,
            cls.module_code.casefold(),
            cls.class_type.casefold(),
            cls.lecturer.casefold(),
            cls.room.casefold(),
            tuple(cls.weeks),
        )

        if key in seen:
            issues.append(
                f"Duplicate class detected: "
                f"{cls.day} "
                f"{cls.start_time}-"
                f"{cls.end_time} "
                f"{cls.module_code}"
            )

        seen.add(key)

    return issues


def extract_with_openai(
    images: list[Path],
) -> TimetableExtraction:
    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY could not be loaded from .env."
        )

    client = OpenAI(
        api_key=api_key
    )

    previous_issues: list[str] | None = None

    for attempt in range(
        1,
        MAX_EXTRACTION_ATTEMPTS + 1,
    ):
        print(
            f"\nReading timetable and searching module names "
            f"(attempt {attempt}/{MAX_EXTRACTION_ATTEMPTS})..."
        )

        content = [
            {
                "type": "input_text",
                "text": build_user_prompt(
                    attempt,
                    previous_issues,
                ),
            }
        ]

        for image in images:
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_as_data_url(
                        image
                    ),
                    "detail": "original",
                }
            )

        response = client.responses.parse(
            model=OPENAI_MODEL,
            tools=[
                {
                    "type": "web_search",
                    "search_context_size": "medium",
                }
            ],
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": content,
                },
            ],
            text_format=TimetableExtraction,
        )

        result = response.output_parsed

        if result is None:
            raise RuntimeError(
                "OpenAI returned no parsed timetable data."
            )

        print(
            f"Found {len(result.classes)} classes."
        )

        unique_modules = sorted(
            {
                cls.module_code.strip().upper()
                for cls in result.classes
            }
        )

        print(
            f"Found {len(unique_modules)} unique modules."
        )

        previous_issues = validate_extraction(
            result
        )

        if not previous_issues:
            return result

        print(
            "\nValidation found:"
        )

        for issue in previous_issues:
            print(
                f"  - {issue}"
            )

        if attempt < MAX_EXTRACTION_ATTEMPTS:
            print(
                "\nRe-reading screenshots..."
            )

    raise RuntimeError(
        "Timetable could not be validated "
        f"after {MAX_EXTRACTION_ATTEMPTS} attempts."
    )


def compress_weeks(
    weeks: list[int],
) -> str:
    weeks = sorted(set(weeks))

    if not weeks:
        return ""

    parts: list[str] = []

    start = weeks[0]
    previous = weeks[0]

    for week in weeks[1:]:
        if week == previous + 1:
            previous = week
            continue

        if start == previous:
            parts.append(
                str(start)
            )
        else:
            parts.append(
                f"{start}-{previous}"
            )

        start = week
        previous = week

    if start == previous:
        parts.append(
            str(start)
        )
    else:
        parts.append(
            f"{start}-{previous}"
        )

    return ", ".join(parts)


def module_text(
    cls: ClassEntry,
) -> str:
    code = cls.module_code.strip().upper()
    name = cls.module_name.strip()

    if name:
        return f"{code} - {name}"

    return code


def event_title(
    cls: ClassEntry,
) -> str:
    return (
        f"{module_text(cls)}"
        f" - {cls.class_type.strip()}"
        f" - {cls.lecturer.strip()}"
    )


def adjusted_end_time(
    end_time: str,
) -> str:
    original = datetime.strptime(
        end_time,
        "%H:%M",
    )

    adjusted = (
        original
        - timedelta(
            minutes=END_EARLY_MINUTES
        )
    )

    return adjusted.strftime(
        "%H:%M"
    )


def print_preview(
    data: TimetableExtraction,
) -> None:
    print(
        "\n"
        + "=" * 110
    )

    print(
        "TIMETABLE PREVIEW"
    )

    print(
        "=" * 110
    )

    classes = sorted(
        data.classes,
        key=lambda cls: (
            DAY_INDEX[cls.day],
            cls.start_time,
            cls.end_time,
        ),
    )

    for number, cls in enumerate(
        classes,
        start=1,
    ):
        adjusted_end = adjusted_end_time(
            cls.end_time
        )

        print(
            f"{number:>2}. "
            f"{cls.day:<9} "
            f"{cls.start_time}-"
            f"{adjusted_end}  "
            f"{event_title(cls)}"
        )

        print(
            f"    Module:    {module_text(cls)}"
        )

        print(
            f"    Professor: {cls.lecturer}"
        )

        print(
            f"    Room:      {cls.room}"
        )

        print(
            f"    Type:      {cls.class_type}"
        )

        print(
            f"    Weeks:     "
            f"{compress_weeks(cls.weeks)}"
        )

        print(
            f"    Timetable: "
            f"{cls.start_time}-{cls.end_time}"
        )

    print(
        "\nTeaching-week dates:"
    )

    for row in sorted(
        data.teaching_weeks,
        key=lambda item: item.week,
    ):
        print(
            f"  Week {row.week:>2}: "
            f"{row.as_date():%A %d %B %Y}"
        )

    print(
        "=" * 110
    )


def parse_hhmm(
    value: str,
) -> time:
    return datetime.strptime(
        value,
        "%H:%M",
    ).time()


def class_date(
    commencing_monday: date,
    day_name: str,
) -> date:
    return (
        commencing_monday
        + timedelta(
            days=DAY_INDEX[day_name]
        )
    )


def make_event(
    cls: ClassEntry,
    week_lookup: dict[int, date],
) -> Event:
    tz = ZoneInfo(
        TIMEZONE
    )

    occurrence_dates = sorted(
        class_date(
            week_lookup[week],
            cls.day,
        )
        for week in cls.weeks
    )

    first_date = occurrence_dates[0]
    last_date = occurrence_dates[-1]

    start_clock = parse_hhmm(
        cls.start_time
    )

    timetable_end_clock = parse_hhmm(
        cls.end_time
    )

    start_dt = datetime.combine(
        first_date,
        start_clock,
        tzinfo=tz,
    )

    timetable_end_dt = datetime.combine(
        first_date,
        timetable_end_clock,
        tzinfo=tz,
    )

    end_dt = (
        timetable_end_dt
        - timedelta(
            minutes=END_EARLY_MINUTES
        )
    )

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
        (
            f"{uuid.uuid5(uuid.NAMESPACE_URL, uid_source)}"
            f"@ul-calendar"
        ),
    )

    event.add(
        "dtstamp",
        datetime.now(
            timezone.utc
        ),
    )

    event.add(
        "summary",
        event_title(cls),
    )

    event.add(
        "dtstart",
        start_dt,
    )

    event.add(
        "dtend",
        end_dt,
    )

    event.add(
        "location",
        cls.room.strip(),
    )

    module_name = (
        cls.module_name.strip()
        if cls.module_name.strip()
        else "Could not verify online"
    )

    description = (
        f"Module code: "
        f"{cls.module_code.strip().upper()}\n"
        f"Module name: "
        f"{module_name}\n"
        f"Class type: "
        f"{cls.class_type.strip()}\n"
        f"Professor: "
        f"{cls.lecturer.strip()}\n"
        f"Room: "
        f"{cls.room.strip()}\n"
        f"Teaching weeks: "
        f"{compress_weeks(cls.weeks)}\n"
        f"Timetable time: "
        f"{cls.start_time}-{cls.end_time}\n"
        f"Calendar time: "
        f"{cls.start_time}-{end_dt.strftime('%H:%M')}"
    )

    event.add(
        "description",
        description,
    )

    until_local = datetime.combine(
        last_date,
        end_dt.time(),
        tzinfo=tz,
    )

    event.add(
        "rrule",
        {
            "freq": "weekly",
            "until": until_local.astimezone(
                timezone.utc
            ),
        },
    )

    valid_dates = set(
        occurrence_dates
    )

    current_date = first_date

    while current_date <= last_date:
        if current_date not in valid_dates:
            event.add(
                "exdate",
                datetime.combine(
                    current_date,
                    start_clock,
                    tzinfo=tz,
                ),
            )

        current_date += timedelta(
            days=7
        )

    return event


def create_ics(
    data: TimetableExtraction,
) -> None:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    week_lookup = {
        row.week: row.as_date()
        for row in data.teaching_weeks
    }

    calendar = Calendar()

    calendar.add(
        "prodid",
        "-//UL Calendar Creator//EN",
    )

    calendar.add(
        "version",
        "2.0",
    )

    calendar.add(
        "calscale",
        "GREGORIAN",
    )

    calendar.add(
        "x-wr-calname",
        "UL Timetable",
    )

    calendar.add(
        "x-wr-timezone",
        TIMEZONE,
    )

    classes = sorted(
        data.classes,
        key=lambda cls: (
            DAY_INDEX[cls.day],
            cls.start_time,
            cls.end_time,
        ),
    )

    for cls in classes:
        calendar.add_component(
            make_event(
                cls,
                week_lookup,
            )
        )

    OUTPUT_FILE.write_bytes(
        calendar.to_ical()
    )


def main() -> None:
    print(
        "=" * 70
    )

    print(
        "UL Timetable -> GPT Vision + Web Search -> classes.ics"
    )

    print(
        "=" * 70
    )

    if not ENV_FILE.exists():
        raise RuntimeError(
            f".env file was not found:\n{ENV_FILE}"
        )

    if not os.getenv(
        "OPENAI_API_KEY"
    ):
        raise RuntimeError(
            "OPENAI_API_KEY could not be loaded from .env."
        )

    print(
        "\nAPI key loaded successfully."
    )

    images = find_input_images()

    print(
        "\nInput images:"
    )

    for image in images:
        print(
            f"  {image.name}"
        )

    data = extract_with_openai(
        images
    )

    print_preview(
        data
    )

    issues = validate_extraction(
        data
    )

    if issues:
        print(
            "\nCalendar was NOT created because "
            "validation failed:"
        )

        for issue in issues:
            print(
                f"  - {issue}"
            )

        sys.exit(1)

    answer = input(
        "\nDoes this preview look correct? "
        "Type YES to create classes.ics: "
    ).strip()

    if answer != "YES":
        print(
            "\nCancelled."
        )

        return

    create_ics(
        data
    )

    print(
        "\nDone."
    )

    print(
        f"\nCreated:\n  {OUTPUT_FILE}"
    )

    print(
        f"\nDetected and exported "
        f"{len(data.classes)} classes."
    )


if __name__ == "__main__":
    try:
        main()

    except ValidationError as exc:
        print(
            "\nStructured extraction validation error:"
        )

        print(exc)

        sys.exit(1)

    except KeyboardInterrupt:
        print(
            "\nCancelled."
        )

        sys.exit(130)

    except Exception as exc:
        print(
            f"\nERROR: {exc}"
        )

        sys.exit(1)