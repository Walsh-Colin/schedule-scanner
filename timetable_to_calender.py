


from __future__ import annotations

import re

import sys
import tempfile
import uuid

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
import cv2
import numpy as np
import ollama
from pydantic import BaseModel, Field, ValidationError, field_validator


BASE_DIR = Path(__file__).resolve().parent

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "classes.ics"
LAYOUT_DEBUG_FILE = OUTPUT_DIR / "layout_debug.png"
OLLAMA_MODEL = "qwen2.5vl:3b"

SEMESTER_YEAR = 2026
TIMEZONE = "Europe/Dublin"

MAX_EXTRACTION_ATTEMPTS = 3
SAVE_LAYOUT_DEBUG = True
END_EARLY_MINUTES = 10


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


class TeachingWeeksExtraction(BaseModel):
    teaching_weeks: list[TeachingWeek]


class ClassBlockRead(BaseModel):
    start_time: str
    end_time: str
    module_code: str
    class_type: str
    lecturer: str
    room: str
    week_text: str

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        value = value.strip()
        datetime.strptime(value, "%H:%M")
        return value

    @field_validator("week_text")
    @classmethod
    def validate_week_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Visible week text is required.")
        return value


class DetectedBlock(BaseModel):
    day: str
    x0: int
    y0: int
    x1: int
    y1: int


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


def _ollama_json(
    *,
    image: Path,
    prompt: str,
    schema_model: type[BaseModel],
) -> BaseModel:
    schema = schema_model.model_json_schema()
    last_error: Exception | None = None

    for attempt in range(1, MAX_EXTRACTION_ATTEMPTS + 1):
        print(
            f"  {image.name}: attempt "
            f"{attempt}/{MAX_EXTRACTION_ATTEMPTS}..."
        )

        retry_note = ""
        if last_error is not None:
            retry_note = (
                "\
\
Your previous response was invalid or incomplete JSON. "
                "Return the COMPLETE JSON object from beginning to end. "
                "Do not add markdown or commentary."
            )

        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": prompt + retry_note,
                    "images": [str(image.resolve())],
                }
            ],
            format=schema,
            options={
                "temperature": 0,
                "num_ctx": 16384,
                "num_predict": 8192,
            },
        )

        raw = response["message"]["content"]

        try:
            return schema_model.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            last_error = exc
            print(f"    Invalid/incomplete JSON; retrying...")

    raise RuntimeError(
        f"{image.name} could not be read as valid structured JSON "
        f"after {MAX_EXTRACTION_ATTEMPTS} attempts.\
"
        f"Last error: {last_error}"
    )




def _group_consecutive(values: np.ndarray) -> list[tuple[int, int]]:
    if len(values) == 0:
        return []

    groups: list[tuple[int, int]] = []
    start = previous = int(values[0])

    for raw in values[1:]:
        value = int(raw)
        if value > previous + 1:
            groups.append((start, previous))
            start = value
        previous = value

    groups.append((start, previous))
    return groups


def detect_day_columns(image: np.ndarray) -> tuple[list[int], int, np.ndarray]:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


    _, binary = cv2.threshold(
        gray,
        225,
        255,
        cv2.THRESH_BINARY_INV,
    )

    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, max(30, height // 12)),
    )
    vertical_lines = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        vertical_kernel,
    )

    vertical_projection = (vertical_lines > 0).sum(axis=0)
    x_candidates = np.where(vertical_projection > height * 0.55)[0]
    x_groups = _group_consecutive(x_candidates)

    boundaries = [
        int(round((left + right) / 2))
        for left, right in x_groups
    ]


    clean_boundaries: list[int] = []
    for x in boundaries:
        if not clean_boundaries or x - clean_boundaries[-1] >= max(20, width // 50):
            clean_boundaries.append(x)

    if len(clean_boundaries) < 6:
        raise RuntimeError(
            "OpenCV could not detect enough timetable day columns. "
            f"Detected boundaries: {clean_boundaries}"
        )


    dark_fraction = (binary > 0).mean(axis=1)
    header_rows = np.where(dark_fraction > 0.60)[0]
    header_groups = _group_consecutive(header_rows)

    header_bottom = max(25, round(height * 0.04))
    for top, bottom in header_groups:
        if top < height * 0.15 and (bottom - top + 1) >= 8:
            header_bottom = bottom + 1
            break

    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(30, width // 18), 1),
    )
    horizontal_lines = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        horizontal_kernel,
    )

    grid = cv2.bitwise_or(vertical_lines, horizontal_lines)
    text_only = cv2.subtract(binary, grid)

    return clean_boundaries, header_bottom, text_only


def detect_class_blocks(
    timetable_image: Path,
) -> tuple[np.ndarray, list[DetectedBlock]]:
    image = cv2.imread(str(timetable_image))
    if image is None:
        raise RuntimeError(f"Could not open timetable image: {timetable_image}")

    height, width = image.shape[:2]
    boundaries, body_top, text_only = detect_day_columns(image)



    interval_count = min(len(boundaries) - 1, 7)
    if interval_count < 5:
        raise RuntimeError(
            f"Expected at least Monday-Friday columns; found {interval_count}."
        )

    blocks: list[DetectedBlock] = []

    kernel_x = max(7, round(width * 0.010))
    kernel_y = max(9, round(height * 0.015))
    group_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (kernel_x, kernel_y),
    )

    for day_index in range(interval_count):
        left = boundaries[day_index]
        right = boundaries[day_index + 1]

        if right - left < 40:
            continue


        inner_left = left + 3
        inner_right = right - 3

        column_mask = text_only[
            body_top:height,
            inner_left:inner_right,
        ]

        grouped = cv2.dilate(
            column_mask,
            group_kernel,
            iterations=1,
        )

        count, _, stats, _ = cv2.connectedComponentsWithStats(
            grouped,
            connectivity=8,
        )

        column_components: list[tuple[int, int, int, int, int]] = []

        min_area = max(180, round(width * height * 0.00022))
        min_width = max(18, round(width * 0.020))
        min_height = max(18, round(height * 0.025))

        for label in range(1, count):
            x, y, w, h, area = [int(v) for v in stats[label]]

            if area < min_area:
                continue
            if w < min_width or h < min_height:
                continue



            column_components.append((x, y, w, h, area))

        column_components.sort(key=lambda item: item[1])

        for x, y, w, h, _ in column_components:
            pad_x = max(7, round(width * 0.008))
            pad_y = max(6, round(height * 0.007))

            x0 = max(left + 1, inner_left + x - pad_x)
            x1 = min(right - 1, inner_left + x + w + pad_x)
            y0 = max(body_top, body_top + y - pad_y)
            y1 = min(height - 1, body_top + y + h + pad_y)

            blocks.append(
                DetectedBlock(
                    day=DAYS[day_index],
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
            )

    blocks.sort(
        key=lambda block: (
            DAY_INDEX.get(block.day, 99),
            block.y0,
            block.x0,
        )
    )

    return image, blocks


def save_layout_debug(
    image: np.ndarray,
    blocks: list[DetectedBlock],
) -> None:
    if not SAVE_LAYOUT_DEBUG:
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    debug = image.copy()

    for index, block in enumerate(blocks, start=1):
        cv2.rectangle(
            debug,
            (block.x0, block.y0),
            (block.x1, block.y1),
            (0, 0, 255),
            2,
        )
        cv2.putText(
            debug,
            f"{index} {block.day}",
            (block.x0 + 2, max(18, block.y0 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )

    cv2.imwrite(str(LAYOUT_DEBUG_FILE), debug)


def make_block_crop(
    image: np.ndarray,
    block: DetectedBlock,
    output_path: Path,
) -> None:
    crop = image[
        block.y0:block.y1,
        block.x0:block.x1,
    ]

    if crop.size == 0:
        raise RuntimeError(f"Empty crop detected for {block.day}.")



    scale = 3
    enlarged = cv2.resize(
        crop,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    cv2.imwrite(str(output_path), enlarged)



def parse_week_text(
    week_text: str,
    valid_week_numbers: set[int] | None = None,
) -> list[int]:
    original = week_text.strip()

    text = original.casefold()
    text = (
        text.replace("weeks", "")
        .replace("week", "")
        .replace("wks", "")
        .replace("wk", "")
        .replace(":", "")
        .replace(";", ",")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )


    text = re.sub(r"[^0-9,\-\s]", "", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", ",", text)
    text = re.sub(r",+", ",", text).strip(",")

    if not text:
        raise ValueError(
            f"Could not parse teaching weeks from {original!r}."
        )

    weeks: list[int] = []

    for part in text.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            pieces = [piece for piece in part.split("-") if piece]
            if len(pieces) != 2:
                raise ValueError(
                    f"Ambiguous week range {part!r} in {original!r}."
                )

            start = int(pieces[0])
            end = int(pieces[1])

            if start > end:
                raise ValueError(
                    f"Reversed week range {part!r} in {original!r}."
                )

            weeks.extend(range(start, end + 1))
        else:
            weeks.append(int(part))

    weeks = sorted(set(weeks))

    if not weeks:
        raise ValueError(
            f"No teaching weeks found in {original!r}."
        )

    if min(weeks) < 1 or max(weeks) > 60:
        raise ValueError(
            f"Invalid teaching week in {original!r}: {weeks}"
        )

    if valid_week_numbers is not None:
        unknown = sorted(set(weeks) - valid_week_numbers)
        if unknown:
            raise ValueError(
                f"Week text {original!r} contains weeks not present "
                f"in the teaching-weeks screenshot: {unknown}"
            )

    return weeks

def extract_with_ollama(
    images: list[Path],
) -> TimetableExtraction:
    week_candidates = [p for p in images if "week" in p.stem.casefold()]
    timetable_candidates = [p for p in images if "timetable" in p.stem.casefold()]

    if len(week_candidates) == 1 and len(timetable_candidates) == 1:
        weeks_image = week_candidates[0]
        timetable_image = timetable_candidates[0]
    else:
        raise RuntimeError(
            "Name the screenshots timetable.png and weeks.png "
            "(jpg/jpeg/webp also work)."
        )

    print(f"\nReading teaching weeks locally with {OLLAMA_MODEL}...")

    weeks_prompt = r"""
You are reading a University of Limerick teaching-week screenshot.

Extract EVERY visible teaching-week row.
For each row return:
- week: integer teaching week number
- day: integer day from the commencing date
- month: integer month from the commencing date

Example: 07/09 means day=7, month=9.
Do not invent a year.
Do not skip rows.
Return only JSON matching the supplied schema.
"""

    weeks_result = _ollama_json(
        image=weeks_image,
        prompt=weeks_prompt,
        schema_model=TeachingWeeksExtraction,
    )

    print(f"Found {len(weeks_result.teaching_weeks)} teaching weeks.")

    valid_week_numbers = {
        item.week
        for item in weeks_result.teaching_weeks
    }

    print("\nDetecting timetable layout with OpenCV...")
    source_image, blocks = detect_class_blocks(timetable_image)

    if not blocks:
        raise RuntimeError("OpenCV did not detect any class blocks.")

    save_layout_debug(source_image, blocks)

    print(f"Detected {len(blocks)} class blocks geometrically.")
    print("Day assignments come from column position, not the LLM.")

    block_prompt = r"""
This image crop contains ONE University of Limerick timetable class block.

Read ONLY the text visible in this one block.

Return:
- start_time: the printed start time in HH:MM 24-hour format
- end_time: the printed end time in HH:MM 24-hour format
- module_code: exact module code, e.g. CS4297
- class_type: the COMPLETE visible class type
- lecturer: lecturer/professor name
- room: exact room text
- week_text: copy the COMPLETE visible week text exactly as written

CLASS TYPE IS IMPORTANT:
- Preserve group suffixes.
- "LAB - 2A" must remain "LAB - 2A".
- "LAB - 2E" must remain "LAB - 2E".
- "TUT - 3D" must remain "TUT - 3D".
- Do not shorten them to LAB or TUT.

TIME IS IMPORTANT:
- Copy the time printed in THIS crop.
- Do not invent a different time.
- Overlapping classes elsewhere are irrelevant because this crop contains
  only one class.

WEEK TEXT IS IMPORTANT:
- Copy the visible week text literally.
- Do NOT expand a range yourself.
- Do NOT assume it ends at week 12.
- "Wks:2-11" must be returned as "Wks:2-11".
- "Wks:6" must be returned as "Wks:6".
- "Wks:1-5,7-9" must preserve both ranges.
- Never replace a visible end week with 12.

Python will interpret the week text after you return it.

Return only JSON matching the supplied schema.
"""

    classes: list[ClassEntry] = []

    with tempfile.TemporaryDirectory(prefix="ul_calendar_blocks_") as temp_dir:
        temp_path = Path(temp_dir)

        for index, block in enumerate(blocks, start=1):
            crop_path = temp_path / f"class_{index:02d}_{block.day}.png"
            make_block_crop(
                source_image,
                block,
                crop_path,
            )

            print(
                f"Reading class {index}/{len(blocks)} "
                f"({block.day}) with {OLLAMA_MODEL}..."
            )

            block_data = _ollama_json(
                image=crop_path,
                prompt=block_prompt,
                schema_model=ClassBlockRead,
            )

            parsed_weeks = parse_week_text(
                block_data.week_text,
                valid_week_numbers,
            )

            print(
                f"  Weeks: {block_data.week_text!r} "
                f"-> {compress_weeks(parsed_weeks)}"
            )

            classes.append(
                ClassEntry(
                    day=block.day,
                    start_time=block_data.start_time,
                    end_time=block_data.end_time,
                    module_code=block_data.module_code.strip().upper(),
                    module_name="",
                    class_type=block_data.class_type.strip(),
                    lecturer=block_data.lecturer.strip(),
                    room=block_data.room.strip(),
                    weeks=parsed_weeks,
                )
            )

    result = TimetableExtraction(
        teaching_weeks=weeks_result.teaching_weeks,
        classes=classes,
    )

    print(f"Found {len(result.classes)} complete classes.")

    issues = validate_extraction(result)
    if issues:
        print("\nValidation found:")
        for issue in issues:
            print(f"  - {issue}")
        raise RuntimeError(
            "Hybrid extraction produced data, but validation failed."
        )

    return result


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
    return cls.module_code.strip().upper()


def event_title(
    cls: ClassEntry,
) -> str:
    return (
        f"{cls.module_code.strip().upper()}"
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
        else "Not set (can be added in the UI)"
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
    print("=" * 70)
    print(f"UL Timetable -> Local Ollama ({OLLAMA_MODEL}) -> classes.ics")
    print("=" * 70)

    try:
        installed = ollama.list()
        model_names = {
            getattr(model, "model", "")
            for model in installed.models
        }
        if not any(
            name == OLLAMA_MODEL or name.startswith(OLLAMA_MODEL + ":")
            for name in model_names
        ):
            raise RuntimeError(
                f"{OLLAMA_MODEL} is not installed.\n"
                f"Run: ollama pull {OLLAMA_MODEL}"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Could not connect to Ollama. Make sure Ollama is installed "
            "and running."
        ) from exc

    print(f"\nLocal model ready: {OLLAMA_MODEL}")

    images = find_input_images()

    print("\nInput images:")
    for image in images:
        print(f"  {image.name}")

    data = extract_with_ollama(images)

    print_preview(data)

    issues = validate_extraction(data)

    if issues:
        print("\nCalendar was NOT created because validation failed:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)

    answer = input(
        "\nDoes this preview look correct? "
        "Type YES to create classes.ics: "
    ).strip().casefold()

    if answer not in {"yes", "y"}:
        print("\nCancelled.")
        return

    create_ics(data)

    print("\nDone.")
    print(f"\nCreated:\n  {OUTPUT_FILE}")
    print(f"\nDetected and exported {len(data.classes)} classes.")


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
