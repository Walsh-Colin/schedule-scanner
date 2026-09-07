# Schedule Scanner Documentation

## Overview

Schedule Scanner converts a weekly timetable screenshot and a teaching-week table screenshot into an iCalendar (`.ics`) file. OpenCV identifies timetable geometry and a local Ollama vision model reads the contents. No API key or Google Calendar API is required.

## Requirements

These requirements apply when running from source. The Windows installer bundles
Python and Ollama, and opens an AI reader setup window to download the model.

- Python 3.10 or newer
- Ollama with the `qwen2.5vl:3b` model
- `ollama`, `pydantic`, `icalendar`, `opencv-python`, `numpy`, `Pillow`, and `customtkinter`

```powershell
python -m pip install --upgrade ollama pydantic icalendar opencv-python numpy Pillow customtkinter
ollama pull qwen2.5vl:3b
```

## Running the application

```powershell
python -m schedule_scanner.schedule_scanner
```

Select both screenshots, create the timetable, review the detected classes, and download the calendar.

## Package structure

- `schedule_scanner/schedule_scanner.py`: application launch entry point
- `schedule_scanner/services/calendar_export.py`: iCalendar event and file generation
- `schedule_scanner/models/timetable.py`: validated Pydantic models
- `schedule_scanner/services/inference_cache.py`: cached inference storage
- `schedule_scanner/services/layout_detection.py`: timetable geometry and image crops
- `schedule_scanner/services/timetable_extraction.py`: Ollama inference, caching coordination, and extraction validation
- `schedule_scanner/services/teaching_weeks.py`: teaching-week parsing and formatting
- `schedule_scanner/ui/editable_class_row.py`: editable timetable review-row component
- `schedule_scanner/ui/extraction_controller.py`: extraction worker, model checks, queue, file signatures, and timer state
- `schedule_scanner/ui/review_view.py`: timetable review layout, row editing, and save validation
- `schedule_scanner/ui/theme.py`: visual theme, application identity, asset paths, and shared UI configuration
- `schedule_scanner/ui/app.py`: desktop application window and UI workflows
- `timetable_to_calender.py`: older standalone implementation

## Extraction pipeline

The application accepts PNG, JPEG, and WebP screenshots. Automatic input discovery expects exactly two images in `input/` and uses filenames containing `week` and `timetable` to distinguish them when possible.

OpenCV detects long vertical lines to establish day columns, progressively relaxing its line-coverage threshold for partial borders. The header band determines where the timetable body begins. Grid lines are removed, remaining text is grouped into connected regions, and each region is assigned a weekday strictly from its geometry. The vision model does not choose weekdays.

Each class region is padded, cropped, enlarged two times, and encoded as PNG bytes in memory. Layout debugging can be enabled in the layout service; annotated detections are then written to `output/layout_debug.png`.

The teaching-week screenshot and individual class crops are processed separately. Ollama responses use Pydantic-generated JSON schemas and are retried up to three times when invalid. Smaller context and output budgets are used for class crops, and the model is kept alive between requests.

## Inference cache

Inference results are cached in the operating system's temporary `ScheduleScanner/inference-cache` directory. Keys include the model, schema, prompt, inference limits, and image bytes. Cache failures do not interrupt extraction, and the UI can clear cached results.

## Teaching weeks and validation

Week text accepts individual values, comma-separated lists, and inclusive ranges. Labels and dash variants are normalized; results are deduplicated, sorted, range-checked, and optionally checked against the teaching-week screenshot.

Validation checks for missing data, duplicate week numbers, Monday start dates, chronological week dates, valid class time ranges, required class fields, unknown teaching weeks, and duplicate classes.

## Calendar output

Events use the `Europe/Dublin` timezone. Each occurrence is derived from its weekday and teaching-week date. Classes finish ten minutes before the displayed timetable end time.

Titles contain the module code, class type, and professor. Descriptions contain the module code and name, class type, professor, room, teaching weeks, original timetable time, and adjusted calendar time.

The command-line output is `output/classes.ics`. The desktop UI builds a temporary calendar and lets the user choose the download location.

## User interface

The CustomTkinter interface supports screenshot selection, background extraction, progress and timing, cached-result reuse, editable class review, row deletion, validation, and calendar download. A queue returns worker-thread results to the Tk event loop so the interface remains responsive.

The local vision model is preloaded on a background thread when the application starts, allowing model loading to overlap with screenshot selection and reducing cold-start extraction latency.

The artwork in `schedule_scanner/images/CalGen.png` is used in the main header and as the cross-platform window icon. Its `CalGen.ico` variant supplies the Windows title-bar and taskbar icon.

## Configuration

Constants in the application and service modules control the Ollama model, timezone, retry count, end-time adjustment, inference budgets, keep-alive period, inference cache, and layout debugging.

## Windows v1.0.0 installer

`packaging/installer.iss` defines a per-user Windows x64 installer with welcome,
installation folder, optional desktop shortcut, file installation, AI model setup,
and finish screens. No administrator access or separate Python/Ollama installation
is required. Model setup opens as a separate window during installation.

The packaged launcher starts its own hidden Ollama process on an available loopback
port. It stores models in `%LOCALAPPDATA%\ScheduleScanner\models` and logs in
`%LOCALAPPDATA%\ScheduleScanner\ollama.log`. It stops only its own process tree on
normal exit. Existing system Ollama installations are left alone.

The setup window checks for `qwen2.5vl:3b`, offers a Download model button, reports
per-file download progress, verifies that the model is installed, and offers Retry
on failure. Closing setup leaves the installed app available; reopening it offers
setup again. Downloads require internet and several GB of free space. Models are
retained on upgrade and uninstall; users can remove the model directory manually
if they no longer need it. Silent installation defers model setup to first launch.

### Building locally

Use a working Windows x64 Python 3.13 installation with Tk, and Inno Setup 6.
Extract the official Ollama Windows amd64 archive into `packaging/vendor/ollama`,
including its `lib` directory and matching `LICENSE`. This build targets
Ollama 0.33.3. Preserve any runtime dependency license notices in the archive.

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r packaging\requirements-build.txt
.\packaging\build.ps1 -Python .\.venv-build\Scripts\python.exe -OllamaDirectory packaging\vendor\ollama
```

Pass `-ISCC <path-to-ISCC.exe>` if the compiler is installed elsewhere.
The script checks native dependencies, runs setup tests, builds the app, copies
the runtime, records exact Python dependencies, and compiles
`release/ScheduleScanner-Setup-v1.0.0.exe`. It prints the SHA256 of the installer.

The manually triggered **Build Windows installer** GitHub Actions workflow builds
the same installer on a Windows runner and uploads it as an artifact. It does not
publish a release. After pushing the changes, select Actions → Build Windows
installer → Run workflow, then download the artifact from the completed run.

Before publishing, test the installer on a Windows machine without Python/Ollama:
complete setup, interrupt and retry a download, restart offline, generate and
export a timetable, and uninstall. Check that a separately running Ollama process
is unaffected. The current installer is unsigned.
