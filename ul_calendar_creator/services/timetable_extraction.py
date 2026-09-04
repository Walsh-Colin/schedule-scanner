from datetime import date, datetime
import hashlib
from pathlib import Path

import ollama
from pydantic import BaseModel, ValidationError

try:
    from ..models import (
        ClassBlockRead,
        ClassEntry,
        TeachingWeeksExtraction,
        TimetableExtraction,
    )
    from .inference_cache import INFERENCE_CACHE_DIR
    from .layout_detection import (
        detect_class_blocks,
        make_block_crop_bytes,
        save_layout_debug,
    )
    from .teaching_weeks import compress_weeks, parse_week_text
except ImportError:
    from models import (
        ClassBlockRead,
        ClassEntry,
        TeachingWeeksExtraction,
        TimetableExtraction,
    )
    from services.inference_cache import INFERENCE_CACHE_DIR
    from services.layout_detection import (
        detect_class_blocks,
        make_block_crop_bytes,
        save_layout_debug,
    )
    from services.teaching_weeks import compress_weeks, parse_week_text


DEFAULT_OLLAMA_MODEL = "qwen2.5vl:3b"
MAX_EXTRACTION_ATTEMPTS = 3
CLASS_NUM_CTX = 4096
CLASS_NUM_PREDICT = 384
WEEKS_NUM_CTX = 6144
WEEKS_NUM_PREDICT = 768
OLLAMA_KEEP_ALIVE = "15m"
USE_INFERENCE_CACHE = True


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
    model: str = DEFAULT_OLLAMA_MODEL,
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
            model,
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
            model=model,
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


def extract_with_ollama(
    images: list[Path] | None = None,
    *,
    timetable_image: Path | None = None,
    weeks_image: Path | None = None,
    model: str = DEFAULT_OLLAMA_MODEL,
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

    print(f"\nReading teaching weeks locally with {model}...")

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
        model=model,
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
            f"({block.day}) with {model}..."
        )

        block_data = _ollama_json(
            model=model,
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
