# UL Calendar Creator Documentation

## Overview

UL Calendar Creator converts a weekly timetable screenshot and a teaching-week table screenshot into an iCalendar (`.ics`) file. OpenCV identifies timetable geometry and a local Ollama vision model reads the contents. No API key or Google Calendar API is required.

## Requirements

- Python 3.10 or newer
- Ollama with the `qwen2.5vl:3b` model
- `ollama`, `pydantic`, `icalendar`, `opencv-python`, `numpy`, `Pillow`, and `customtkinter`

```powershell
python -m pip install --upgrade ollama pydantic icalendar opencv-python numpy Pillow customtkinter
ollama pull qwen2.5vl:3b
```

## Running the application

```powershell
python -m ul_calendar_creator.ul_calendar_creator
```

Select both screenshots, create the timetable, review the detected classes, and download the calendar.

## Package structure

- `ul_calendar_creator/ul_calendar_creator.py`: application launch entry point
- `ul_calendar_creator/services/calendar_export.py`: iCalendar event and file generation
- `ul_calendar_creator/models/timetable.py`: validated Pydantic models
- `ul_calendar_creator/services/inference_cache.py`: cached inference storage
- `ul_calendar_creator/services/layout_detection.py`: timetable geometry and image crops
- `ul_calendar_creator/services/timetable_extraction.py`: Ollama inference, caching coordination, and extraction validation
- `ul_calendar_creator/services/teaching_weeks.py`: teaching-week parsing and formatting
- `ul_calendar_creator/ui/editable_class_row.py`: editable timetable review-row component
- `ul_calendar_creator/ui/app.py`: desktop application window and UI workflows
- `timetable_to_calender.py`: older standalone implementation

## Extraction pipeline

The application accepts PNG, JPEG, and WebP screenshots. Automatic input discovery expects exactly two images in `input/` and uses filenames containing `week` and `timetable` to distinguish them when possible.

OpenCV detects long vertical lines to establish day columns, progressively relaxing its line-coverage threshold for partial borders. The header band determines where the timetable body begins. Grid lines are removed, remaining text is grouped into connected regions, and each region is assigned a weekday strictly from its geometry. The vision model does not choose weekdays.

Each class region is padded, cropped, enlarged three times, and encoded as PNG bytes in memory. Layout debugging can be enabled in the layout service; annotated detections are then written to `output/layout_debug.png`.

The teaching-week screenshot and individual class crops are processed separately. Ollama responses use Pydantic-generated JSON schemas and are retried up to three times when invalid. Smaller context and output budgets are used for class crops, and the model is kept alive between requests.

## Inference cache

Inference results are cached in the operating system's temporary `ULCalendarCreator/inference-cache` directory. Keys include the model, schema, prompt, inference limits, and image bytes. Cache failures do not interrupt extraction, and the UI can clear cached results.

## Teaching weeks and validation

Week text accepts individual values, comma-separated lists, and inclusive ranges. Labels and dash variants are normalized; results are deduplicated, sorted, range-checked, and optionally checked against the teaching-week screenshot.

Validation checks for missing data, duplicate week numbers, Monday start dates, chronological week dates, valid class time ranges, required class fields, unknown teaching weeks, and duplicate classes.

## Calendar output

Events use the `Europe/Dublin` timezone. Each occurrence is derived from its weekday and teaching-week date. Classes finish ten minutes before the displayed timetable end time.

Titles contain the module code, class type, and professor. Descriptions contain the module code and name, class type, professor, room, teaching weeks, original timetable time, and adjusted calendar time.

The command-line output is `output/classes.ics`. The desktop UI builds a temporary calendar and lets the user choose the download location.

## User interface

The CustomTkinter interface supports screenshot selection, background extraction, progress and timing, cached-result reuse, editable class review, row deletion, validation, and calendar download. A queue returns worker-thread results to the Tk event loop so the interface remains responsive.

The artwork in `ul_calendar_creator/images/CalGen.png` is used in the main header and as the cross-platform window icon. Its `CalGen.ico` variant supplies the Windows title-bar and taskbar icon.

## Configuration

Constants in the application and service modules control the Ollama model, timezone, retry count, end-time adjustment, inference budgets, keep-alive period, inference cache, and layout debugging.
