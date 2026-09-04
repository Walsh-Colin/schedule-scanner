


from __future__ import annotations

import time as time_module
import os
import hashlib
import shutil
import threading
import queue
import webbrowser

import sys
import tempfile
import uuid

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
import cv2
import numpy as np
import ollama
import customtkinter as ctk
from tkinter import filedialog, messagebox
from pydantic import BaseModel, ValidationError

if __package__:
    from .models import (
        ClassBlockRead,
        ClassEntry,
        DetectedBlock,
        TeachingWeek,
        TeachingWeeksExtraction,
        TimetableExtraction,
    )
    from .services.inference_cache import (
        INFERENCE_CACHE_DIR,
        clear_inference_cache,
    )
    from .services.teaching_weeks import compress_weeks, parse_week_text
else:

    from models import (
        ClassBlockRead,
        ClassEntry,
        DetectedBlock,
        TeachingWeek,
        TeachingWeeksExtraction,
        TimetableExtraction,
    )
    from services.inference_cache import (
        INFERENCE_CACHE_DIR,
        clear_inference_cache,
    )
    from services.teaching_weeks import compress_weeks, parse_week_text


BASE_DIR = Path(__file__).resolve().parent.parent

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "classes.ics"
LAYOUT_DEBUG_FILE = OUTPUT_DIR / "layout_debug.png"
OLLAMA_MODEL = "qwen2.5vl:3b"

TIMEZONE = "Europe/Dublin"

MAX_EXTRACTION_ATTEMPTS = 3
SAVE_LAYOUT_DEBUG = False
END_EARLY_MINUTES = 10





CLASS_NUM_CTX = 4096
CLASS_NUM_PREDICT = 384
WEEKS_NUM_CTX = 6144
WEEKS_NUM_PREDICT = 768
OLLAMA_KEEP_ALIVE = "15m"
USE_INFERENCE_CACHE = True


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
    image: Path | bytes,
    prompt: str,
    schema_model: type[BaseModel],
    image_name: str | None = None,
) -> BaseModel:
    schema = schema_model.model_json_schema()
    last_error: Exception | None = None

    if isinstance(image, Path):
        image_payload: str | bytes = str(image.resolve())
        display_name = image.name
    else:
        image_payload = image
        display_name = image_name or "image"

    if schema_model is TeachingWeeksExtraction:
        num_ctx = WEEKS_NUM_CTX
        num_predict = WEEKS_NUM_PREDICT
    else:
        num_ctx = CLASS_NUM_CTX
        num_predict = CLASS_NUM_PREDICT

    cache_file: Path | None = None
    if USE_INFERENCE_CACHE:
        image_bytes = image.read_bytes() if isinstance(image, Path) else image
        cache_key = hashlib.sha256()
        for value in (
            OLLAMA_MODEL,
            schema_model.__name__,
            prompt,
            repr(schema),
            str(num_ctx),
            str(num_predict),
        ):
            cache_key.update(value.encode("utf-8"))
            cache_key.update(b"\0")
        cache_key.update(image_bytes)

        cache_file = INFERENCE_CACHE_DIR / f"{cache_key.hexdigest()}.json"

        try:
            cached = schema_model.model_validate_json(
                cache_file.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError):
            pass
        else:
            print(f"  {display_name}: using cached result.")
            return cached

    for attempt in range(1, MAX_EXTRACTION_ATTEMPTS + 1):
        print(
            f"  {display_name}: attempt "
            f"{attempt}/{MAX_EXTRACTION_ATTEMPTS}..."
        )

        retry_note = ""
        if last_error is not None:
            retry_note = (
                "\n\nYour previous response was invalid or incomplete JSON. "
                "Return the COMPLETE JSON object from beginning to end. "
                "Do not add markdown or commentary."
            )

        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": prompt + retry_note,
                    "images": [image_payload],
                }
            ],
            format=schema,
            keep_alive=OLLAMA_KEEP_ALIVE,
            options={
                "temperature": 0,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        )

        raw = response["message"]["content"]

        try:
            validated = schema_model.model_validate_json(raw)

            if cache_file is not None:
                try:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    temporary_cache_file = cache_file.with_suffix(".tmp")
                    temporary_cache_file.write_text(
                        validated.model_dump_json(),
                        encoding="utf-8",
                    )
                    temporary_cache_file.replace(cache_file)
                except OSError:

                    pass

            return validated
        except (ValidationError, ValueError) as exc:
            last_error = exc
            print("    Invalid/incomplete JSON; retrying...")

    raise RuntimeError(
        f"{display_name} could not be read as valid structured JSON "
        f"after {MAX_EXTRACTION_ATTEMPTS} attempts.\n"
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




    clean_boundaries: list[int] = []
    projection_fraction = 0.55
    for candidate_fraction in (0.55, 0.45, 0.35, 0.25):
        x_candidates = np.where(
            vertical_projection > height * candidate_fraction
        )[0]
        x_groups = _group_consecutive(x_candidates)
        boundaries = [
            int(round((left + right) / 2))
            for left, right in x_groups
        ]

        candidate_boundaries: list[int] = []
        for x in boundaries:
            if (
                not candidate_boundaries
                or x - candidate_boundaries[-1] >= max(20, width // 50)
            ):
                candidate_boundaries.append(x)

        clean_boundaries = candidate_boundaries
        projection_fraction = candidate_fraction
        if len(clean_boundaries) >= 6:
            break

    if len(clean_boundaries) < 6:
        raise RuntimeError(
            "OpenCV could not detect enough timetable day columns. "
            f"Detected boundaries: {clean_boundaries} "
            f"(minimum line coverage tried: {projection_fraction:.0%})."
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


def make_block_crop_bytes(
    image: np.ndarray,
    block: DetectedBlock,
) -> bytes:
    crop = image[
        block.y0:block.y1,
        block.x0:block.x1,
    ]

    if crop.size == 0:
        raise RuntimeError(f"Empty crop detected for {block.day}.")

    enlarged = cv2.resize(
        crop,
        None,
        fx=3,
        fy=3,
        interpolation=cv2.INTER_CUBIC,
    )

    ok, encoded = cv2.imencode(
        ".png",
        enlarged,
        [cv2.IMWRITE_PNG_COMPRESSION, 3],
    )

    if not ok:
        raise RuntimeError(
            f"Could not encode class crop for {block.day}."
        )

    return encoded.tobytes()



if __package__:
    from .services.layout_detection import (
        detect_class_blocks,
        make_block_crop_bytes,
        save_layout_debug,
    )
else:
    from services.layout_detection import (
        detect_class_blocks,
        make_block_crop_bytes,
        save_layout_debug,
    )


if __package__:
    from .services.layout_detection import (
        detect_class_blocks,
        make_block_crop_bytes,
        save_layout_debug,
    )
else:
    from services.layout_detection import (
        detect_class_blocks,
        make_block_crop_bytes,
        save_layout_debug,
    )


def extract_with_ollama(
    images: list[Path] | None = None,
    *,
    timetable_image: Path | None = None,
    weeks_image: Path | None = None,
) -> TimetableExtraction:
    if timetable_image is not None and weeks_image is not None:
        timetable_image = Path(timetable_image)
        weeks_image = Path(weeks_image)
    elif images is not None:
        week_candidates = [
            p for p in images
            if "week" in p.stem.casefold()
        ]
        timetable_candidates = [
            p for p in images
            if "timetable" in p.stem.casefold()
        ]

        if (
            len(week_candidates) == 1
            and len(timetable_candidates) == 1
        ):
            weeks_image = week_candidates[0]
            timetable_image = timetable_candidates[0]
        else:
            raise RuntimeError(
                "Could not identify the timetable and weeks screenshots."
            )
    else:
        raise RuntimeError(
            "Timetable and teaching-weeks screenshots are required."
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

    for index, block in enumerate(blocks, start=1):
        crop_bytes = make_block_crop_bytes(
            source_image,
            block,
        )

        print(
            f"Reading class {index}/{len(blocks)} "
            f"({block.day}) with {OLLAMA_MODEL}..."
        )

        block_data = _ollama_json(
            image=crop_bytes,
            image_name=f"class_{index:02d}_{block.day}.png",
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
    output_file: Path | None = None,
) -> Path:
    if output_file is None:
        output_file = OUTPUT_FILE

    output_file = Path(output_file)
    output_file.parent.mkdir(
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

    output_file.write_bytes(
        calendar.to_ical()
    )

    return output_file







APP_TITLE = "UL Calendar Creator"
HIDDEN_MODEL = "qwen2.5vl:3b"

BG = "#f5f7fb"
CARD = "#ffffff"
BORDER = "#d9e1ec"
TEXT = "#10213a"
MUTED = "#66758a"
BLUE = "#1769e0"
BLUE_HOVER = "#0f58c2"
LIGHT_BLUE = "#eef5ff"
DANGER = "#b42318"
DANGER_HOVER = "#8f1c13"


class TextRedirector:
    def __init__(self, message_queue: queue.Queue):
        self.message_queue = message_queue

    def write(self, value: str) -> None:
        if value and value.strip():
            self.message_queue.put(("engine_log", value.strip()))

    def flush(self) -> None:
        pass


class EditableClassRow:
    def __init__(self, parent, cls: ClassEntry, on_delete) -> None:
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")

        self.day = ctk.StringVar(value=cls.day)
        self.start = ctk.StringVar(value=cls.start_time)
        self.end = ctk.StringVar(value=cls.end_time)
        self.code = ctk.StringVar(value=cls.module_code)
        self.class_type = ctk.StringVar(value=cls.class_type)
        self.lecturer = ctk.StringVar(value=cls.lecturer)
        self.room = ctk.StringVar(value=cls.room)
        self.weeks = ctk.StringVar(value=compress_weeks(cls.weeks))

        ctk.CTkOptionMenu(
            self.frame,
            variable=self.day,
            values=DAYS,
            width=100,
        ).grid(row=0, column=0, padx=3, pady=4)

        specs = [
            (self.start, 72),
            (self.end, 72),
            (self.code, 90),
            (self.class_type, 130),
            (self.lecturer, 165),
            (self.room, 105),
            (self.weeks, 105),
        ]

        for col, (var, width) in enumerate(specs, start=1):
            ctk.CTkEntry(
                self.frame,
                textvariable=var,
                width=width,
            ).grid(row=0, column=col, padx=3, pady=4)

        ctk.CTkButton(
            self.frame,
            text="×",
            width=34,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            command=lambda: on_delete(self),
        ).grid(row=0, column=8, padx=(6, 2), pady=4)

    def grid(self, row: int) -> None:
        self.frame.grid(
            row=row,
            column=0,
            sticky="w",
            padx=2,
            pady=1,
        )

    def destroy(self) -> None:
        self.frame.destroy()

    def to_class_entry(self, valid_weeks: set[int]) -> ClassEntry:
        return ClassEntry(
            day=self.day.get().strip(),
            start_time=self.start.get().strip(),
            end_time=self.end.get().strip(),
            module_code=self.code.get().strip().upper(),
            module_name="",
            class_type=self.class_type.get().strip(),
            lecturer=self.lecturer.get().strip(),
            room=self.room.get().strip(),
            weeks=parse_week_text(
                self.weeks.get().strip(),
                valid_weeks,
            ),
        )


class ULCalendarApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.configure(fg_color=BG)

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        w = min(1120, max(760, sw - 100))
        h = min(760, max(580, sh - 120))

        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)

        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(min(760, w), min(580, h))

        self.timetable_path = ctk.StringVar()
        self.weeks_path = ctk.StringVar()
        self.status_text = ctk.StringVar(
            value="Add both screenshots to begin."
        )

        self.extraction: TimetableExtraction | None = None
        self.generated_ics: Path | None = None
        self.ui_queue: queue.Queue = queue.Queue()
        self.class_rows: list[EditableClassRow] = []


        self._model_checked = False
        self._extraction_signature: tuple | None = None



        self._timer_started_at: float | None = None
        self._timer_running = False
        self.timer_text = ctk.StringVar(value="Time: 0.0s")

        self._build_shell()
        self._show_main_view()
        self.after(100, self._drain_queue)





    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.outer = ctk.CTkFrame(
            self,
            fg_color=CARD,
            corner_radius=18,
            border_width=1,
            border_color=BORDER,
        )
        self.outer.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=28,
            pady=28,
        )
        self.outer.grid_columnconfigure(0, weight=1)
        self.outer.grid_rowconfigure(1, weight=1)

        self.header = ctk.CTkFrame(
            self.outer,
            fg_color="transparent",
        )
        self.header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=34,
            pady=(28, 22),
        )
        self.header.grid_columnconfigure(1, weight=1)

        self.header_icon = ctk.CTkLabel(
            self.header,
            text="▣",
            width=72,
            height=72,
            corner_radius=18,
            fg_color=LIGHT_BLUE,
            text_color=BLUE,
            font=ctk.CTkFont(size=34, weight="bold"),
        )
        self.header_icon.grid(
            row=0,
            column=0,
            rowspan=2,
            padx=(0, 22),
        )

        self.header_title = ctk.CTkLabel(
            self.header,
            text="Create Timetable",
            text_color=TEXT,
            font=ctk.CTkFont(size=30, weight="bold"),
            anchor="w",
        )
        self.header_title.grid(
            row=0,
            column=1,
            sticky="w",
            pady=(2, 0),
        )

        self.header_subtitle = ctk.CTkLabel(
            self.header,
            text="Add your screenshots, generate the timetable, review it, then download your calendar.",
            text_color=MUTED,
            font=ctk.CTkFont(size=15),
            anchor="w",
            wraplength=820,
        )
        self.header_subtitle.grid(
            row=1,
            column=1,
            sticky="w",
            pady=(0, 2),
        )

        self.clear_cache_button = ctk.CTkButton(
            self.header,
            text="Developer: Clear Cache",
            width=170,
            height=38,
            fg_color="transparent",
            hover_color="#edf3fb",
            text_color=BLUE,
            border_width=1,
            border_color=BLUE,
            font=ctk.CTkFont(size=13),
            command=self._clear_developer_cache,
        )
        self.clear_cache_button.grid(
            row=0,
            column=2,
            rowspan=2,
            sticky="e",
            padx=(20, 0),
        )

        ctk.CTkFrame(
            self.outer,
            height=1,
            fg_color=BORDER,
        ).grid(
            row=1,
            column=0,
            sticky="new",
        )

        self.content_host = ctk.CTkFrame(
            self.outer,
            fg_color="transparent",
        )
        self.content_host.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=34,
            pady=(28, 22),
        )
        self.content_host.grid_columnconfigure(0, weight=1)
        self.content_host.grid_rowconfigure(0, weight=1)

        ctk.CTkFrame(
            self.outer,
            height=1,
            fg_color=BORDER,
        ).grid(
            row=2,
            column=0,
            sticky="ew",
        )

        self.footer = ctk.CTkFrame(
            self.outer,
            fg_color="transparent",
        )
        self.footer.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=34,
            pady=24,
        )

    def _clear_content(self) -> None:
        for widget in self.content_host.winfo_children():
            widget.destroy()
        for widget in self.footer.winfo_children():
            widget.destroy()

    def _clear_developer_cache(self) -> None:
        if self._timer_running:
            messagebox.showinfo(
                APP_TITLE,
                "Please wait for the current timetable extraction to finish.",
            )
            return

        try:
            clear_inference_cache()
        except OSError as exc:
            messagebox.showerror(
                APP_TITLE,
                f"Could not clear the inference cache:\n\n{exc}",
            )
            return

        self.extraction = None
        self.generated_ics = None
        self._extraction_signature = None
        self.timer_text.set("Time: 0.0s")
        self.status_text.set(
            "Cache cleared. Create the timetable to run a fresh test."
        )
        self._show_main_view()
        messagebox.showinfo(APP_TITLE, "Developer cache cleared.")





    def _show_main_view(self) -> None:
        self._clear_content()

        self.header_title.configure(text="Create Timetable")
        self.header_subtitle.configure(
            text="Add your screenshots, generate the timetable, review it, then download your calendar."
        )

        content = ctk.CTkFrame(
            self.content_host,
            fg_color="transparent",
        )
        content.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        content.grid_columnconfigure(1, weight=1)

        self._build_upload_row(
            content,
            row=0,
            title="Timetable",
            subtitle=(
                f"✓  {Path(self.timetable_path.get()).name}"
                if self.timetable_path.get()
                else "Click to add timetable screenshot"
            ),
            icon_text="+",
            click_command=self._choose_timetable,
            label_attr="timetable_value_label",
        )

        self._build_upload_row(
            content,
            row=1,
            title="Teaching Weeks",
            subtitle=(
                f"✓  {Path(self.weeks_path.get()).name}"
                if self.weeks_path.get()
                else "Click to add teaching weeks screenshot"
            ),
            icon_text="+",
            click_command=self._choose_weeks,
            label_attr="weeks_value_label",
        )

        self.progress = ctk.CTkProgressBar(
            content,
            height=8,
        )
        self.progress.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(14, 8),
        )
        self.progress.set(1 if self.extraction else 0)

        status_row = ctk.CTkFrame(
            content,
            fg_color="transparent",
        )
        status_row.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 2),
        )
        status_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            status_row,
            textvariable=self.status_text,
            text_color=MUTED,
            font=ctk.CTkFont(size=13),
            anchor="w",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        ctk.CTkLabel(
            status_row,
            textvariable=self.timer_text,
            text_color=MUTED,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="e",
        ).grid(
            row=0,
            column=1,
            sticky="e",
            padx=(16, 0),
        )

        for col in range(3):
            self.footer.grid_columnconfigure(col, weight=1)

        self.create_button = ctk.CTkButton(
            self.footer,
            text="✦  Create Timetable",
            height=54,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._start_extraction,
        )
        self.create_button.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 10),
        )

        self.review_button = ctk.CTkButton(
            self.footer,
            text="Review Timetable",
            height=54,
            state=("normal" if self.extraction else "disabled"),
            fg_color="transparent",
            hover_color="#edf3fb",
            text_color=BLUE,
            border_width=1,
            border_color=BLUE,
            font=ctk.CTkFont(size=16),
            command=self._show_review_view,
        )
        self.review_button.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=10,
        )

        self.download_button = ctk.CTkButton(
            self.footer,
            text="Download Calendar",
            height=54,
            state=("normal" if self.extraction else "disabled"),
            fg_color="transparent",
            hover_color="#edf3fb",
            text_color=BLUE,
            border_width=1,
            border_color=BLUE,
            font=ctk.CTkFont(size=16),
            command=self._download_calendar,
        )
        self.download_button.grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(10, 0),
        )

    def _build_upload_row(
        self,
        parent,
        row: int,
        title: str,
        subtitle: str,
        icon_text: str,
        click_command,
        label_attr: str,
    ) -> None:
        icon = ctk.CTkLabel(
            parent,
            text=icon_text,
            width=70,
            height=70,
            corner_radius=18,
            fg_color=LIGHT_BLUE,
            text_color=BLUE,
            font=ctk.CTkFont(size=34, weight="bold"),
        )
        icon.grid(
            row=row,
            column=0,
            padx=(0, 20),
            pady=16,
        )

        wrap = ctk.CTkFrame(
            parent,
            fg_color="transparent",
        )
        wrap.grid(
            row=row,
            column=1,
            sticky="ew",
            pady=16,
        )
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            wrap,
            text=title,
            text_color=TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 7),
        )

        button = ctk.CTkButton(
            wrap,
            text=subtitle,
            height=60,
            anchor="w",
            fg_color=CARD,
            hover_color="#f8fbff",
            text_color=(TEXT if subtitle.startswith("✓") else MUTED),
            border_width=1,
            border_color=BORDER,
            font=ctk.CTkFont(size=15),
            command=click_command,
        )
        button.grid(
            row=1,
            column=0,
            sticky="ew",
        )

        setattr(self, label_attr, button)





    def _show_review_view(self) -> None:
        if self.extraction is None:
            messagebox.showerror(
                APP_TITLE,
                "Create the timetable first.",
            )
            return

        self._clear_content()

        self.header_title.configure(text="Review Timetable")
        self.header_subtitle.configure(
            text="Edit any detected class details below before downloading your calendar."
        )

        review = ctk.CTkFrame(
            self.content_host,
            fg_color="transparent",
        )
        review.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        review.grid_columnconfigure(0, weight=1)
        review.grid_rowconfigure(1, weight=1)

        header_row = ctk.CTkFrame(
            review,
            fg_color="transparent",
        )
        header_row.grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 4),
        )

        headers = [
            ("Day", 100),
            ("Start", 72),
            ("End", 72),
            ("Code", 90),
            ("Class type", 130),
            ("Professor", 165),
            ("Room", 105),
            ("Weeks", 105),
        ]

        for col, (label, width) in enumerate(headers):
            ctk.CTkLabel(
                header_row,
                text=label,
                text_color=TEXT,
                width=width,
                font=ctk.CTkFont(weight="bold"),
            ).grid(
                row=0,
                column=col,
                padx=3,
                pady=4,
            )

        self.rows_frame = ctk.CTkScrollableFrame(
            review,
            fg_color="transparent",
        )
        self.rows_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
        )

        self.class_rows.clear()

        classes = sorted(
            self.extraction.classes,
            key=lambda cls: (
                DAY_INDEX[cls.day],
                cls.start_time,
                cls.end_time,
            ),
        )

        for cls in classes:
            row = EditableClassRow(
                self.rows_frame,
                cls,
                self._delete_review_row,
            )
            self.class_rows.append(row)

        self._regrid_review_rows()

        self.footer.grid_columnconfigure(0, weight=1)
        self.footer.grid_columnconfigure(1, weight=0)
        self.footer.grid_columnconfigure(2, weight=0)

        ctk.CTkButton(
            self.footer,
            text="←  Back",
            width=140,
            height=52,
            fg_color="transparent",
            hover_color="#edf3fb",
            text_color=TEXT,
            border_width=1,
            border_color=BORDER,
            command=self._show_main_view,
        ).grid(
            row=0,
            column=1,
            padx=(0, 10),
        )

        ctk.CTkButton(
            self.footer,
            text="Save Changes",
            width=170,
            height=52,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._save_review_changes,
        ).grid(
            row=0,
            column=2,
        )

    def _delete_review_row(
        self,
        row: EditableClassRow,
    ) -> None:
        if row in self.class_rows:
            self.class_rows.remove(row)
            row.destroy()
            self._regrid_review_rows()

    def _regrid_review_rows(self) -> None:
        for index, row in enumerate(self.class_rows):
            row.grid(index)

    def _save_review_changes(self) -> None:
        try:
            if self.extraction is None:
                raise RuntimeError("No timetable is loaded.")

            valid_weeks = {
                week.week
                for week in self.extraction.teaching_weeks
            }

            classes = [
                row.to_class_entry(valid_weeks)
                for row in self.class_rows
            ]

            if not classes:
                raise RuntimeError(
                    "There are no classes to save."
                )

            edited = TimetableExtraction(
                teaching_weeks=self.extraction.teaching_weeks,
                classes=classes,
            )

            issues = validate_extraction(edited)
            if issues:
                raise RuntimeError(
                    "Please fix these timetable details:\n\n"
                    + "\n".join(
                        f"• {issue}"
                        for issue in issues
                    )
                )

            self.extraction = edited
            self.generated_ics = None
            self.status_text.set(
                f"Changes saved — {len(edited.classes)} classes ready."
            )
            self._show_main_view()

        except (ValidationError, ValueError, RuntimeError) as exc:
            messagebox.showerror(
                "Cannot save changes",
                str(exc),
            )





    @staticmethod
    def _image_filetypes():
        return [
            ("Images", "*.png *.jpg *.jpeg *.webp"),
            ("PNG", "*.png"),
            ("JPEG", "*.jpg *.jpeg"),
            ("WebP", "*.webp"),
            ("All files", "*.*"),
        ]

    def _choose_timetable(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose timetable screenshot",
            filetypes=self._image_filetypes(),
        )
        if not path:
            return

        self.timetable_path.set(path)
        self._reset_after_file_change()
        self._show_main_view()

    def _choose_weeks(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose teaching weeks screenshot",
            filetypes=self._image_filetypes(),
        )
        if not path:
            return

        self.weeks_path.set(path)
        self._reset_after_file_change()
        self._show_main_view()

    def _reset_after_file_change(self) -> None:
        self.extraction = None
        self.generated_ics = None
        self._extraction_signature = None
        self._timer_running = False
        self._timer_started_at = None
        self.timer_text.set("Time: 0.0s")

        if self.timetable_path.get() and self.weeks_path.get():
            self.status_text.set(
                "Ready to create timetable."
            )
        else:
            self.status_text.set(
                "Add both screenshots to begin."
            )





    def _start_timer(self) -> None:
        self._timer_started_at = time_module.perf_counter()
        self._timer_running = True
        self.timer_text.set("Time: 0.0s")
        self.after(0, self._tick_timer)

    def _tick_timer(self) -> None:
        if not self._timer_running or self._timer_started_at is None:
            return

        elapsed = time_module.perf_counter() - self._timer_started_at
        self.timer_text.set(f"Time: {elapsed:.1f}s")


        self.after(100, self._tick_timer)

    def _stop_timer(self) -> float:
        if self._timer_started_at is None:
            self._timer_running = False
            return 0.0

        elapsed = time_module.perf_counter() - self._timer_started_at
        self._timer_running = False
        self._timer_started_at = None
        self.timer_text.set(f"Time: {elapsed:.1f}s")
        return elapsed





    @staticmethod
    def _file_signature(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return (
            str(path.resolve()),
            stat.st_size,
            stat.st_mtime_ns,
        )

    def _start_extraction(self) -> None:
        timetable = Path(self.timetable_path.get().strip())
        weeks = Path(self.weeks_path.get().strip())

        if not timetable.is_file():
            messagebox.showerror(
                APP_TITLE,
                "Please add your timetable screenshot.",
            )
            return

        if not weeks.is_file():
            messagebox.showerror(
                APP_TITLE,
                "Please add your teaching-weeks screenshot.",
            )
            return

        signature = (
            self._file_signature(timetable),
            self._file_signature(weeks),
        )

        if (
            self.extraction is not None
            and self._extraction_signature == signature
        ):
            self.timer_text.set("Time: 0.0s (cached)")
            self.status_text.set(
                f"Timetable already up to date — "
                f"{len(self.extraction.classes)} classes found."
            )
            self.review_button.configure(state="normal")
            self.download_button.configure(state="normal")
            return

        self.extraction = None
        self.generated_ics = None
        self.review_button.configure(state="disabled")
        self.download_button.configure(state="disabled")
        self.create_button.configure(state="disabled")

        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status_text.set("Reading timetable…")
        self._start_timer()

        threading.Thread(
            target=self._extraction_worker,
            args=(timetable, weeks),
            daemon=True,
        ).start()

    def _extraction_worker(
        self,
        timetable: Path,
        weeks: Path,
    ) -> None:
        global OLLAMA_MODEL

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirector = TextRedirector(self.ui_queue)

        try:
            sys.stdout = redirector
            sys.stderr = redirector

            OLLAMA_MODEL = HIDDEN_MODEL

            if not self._model_checked:
                installed = ollama.list()
                model_names = {
                    getattr(item, "model", "")
                    for item in installed.models
                }

                if not any(
                    name == HIDDEN_MODEL
                    or name.startswith(HIDDEN_MODEL + ":")
                    for name in model_names
                ):
                    raise RuntimeError(
                        "The local timetable reader is not installed yet.\n\n"
                        "Run this once in PowerShell:\n"
                        f"ollama pull {HIDDEN_MODEL}"
                    )

                self._model_checked = True

            result = extract_with_ollama(
                timetable_image=timetable,
                weeks_image=weeks,
            )

            self.ui_queue.put(("success", result))

        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()

                if kind == "engine_log":
                    continue

                if kind == "success":


                    elapsed = self._stop_timer()
                    self.extraction = payload

                    try:
                        timetable = Path(self.timetable_path.get().strip())
                        weeks = Path(self.weeks_path.get().strip())
                        self._extraction_signature = (
                            self._file_signature(timetable),
                            self._file_signature(weeks),
                        )
                    except OSError:
                        self._extraction_signature = None

                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1)

                    self.create_button.configure(state="normal")
                    self.review_button.configure(state="normal")
                    self.download_button.configure(state="normal")

                    self.status_text.set(
                        f"Timetable ready — {len(payload.classes)} classes found "
                        f"in {elapsed:.1f}s."
                    )

                elif kind == "error":
                    self._stop_timer()
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(0)

                    self.create_button.configure(state="normal")
                    self.review_button.configure(state="disabled")
                    self.download_button.configure(state="disabled")

                    self.status_text.set(
                        "Could not create timetable."
                    )

                    messagebox.showerror(
                        "Could not create timetable",
                        payload,
                    )

        except queue.Empty:
            pass

        self.after(100, self._drain_queue)





    def _build_ics(self) -> Path:
        if self.extraction is None:
            raise RuntimeError(
                "Create the timetable first."
            )

        generated_dir = (
            Path(tempfile.gettempdir())
            / "ULCalendarCreator"
        )
        generated_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        generated_file = generated_dir / "classes.ics"

        create_ics(
            self.extraction,
            generated_file,
        )

        self.generated_ics = generated_file
        return generated_file

    def _download_calendar(self) -> None:
        try:
            generated = self._build_ics()

            destination = filedialog.asksaveasfilename(
                title="Download calendar",
                defaultextension=".ics",
                initialfile="classes.ics",
                filetypes=[
                    ("iCalendar", "*.ics"),
                    ("All files", "*.*"),
                ],
            )

            if not destination:
                return

            shutil.copy2(
                generated,
                destination,
            )

            self.status_text.set(
                f"Downloaded {Path(destination).name}."
            )

        except (ValidationError, ValueError, RuntimeError) as exc:
            messagebox.showerror(
                "Cannot download calendar",
                str(exc),
            )


def main() -> None:
    app = ULCalendarApp()
    app.mainloop()


if __name__ == "__main__":
    main()
