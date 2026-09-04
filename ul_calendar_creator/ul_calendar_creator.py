from __future__ import annotations

import time as time_module
import os
import ctypes
import shutil
import threading
import queue
import webbrowser

import sys
import tempfile
from datetime import datetime
from pathlib import Path
import ollama
import customtkinter as ctk
from PIL import Image
from tkinter import PhotoImage, filedialog, messagebox
from pydantic import ValidationError

if __package__:
    from .models import (
        ClassEntry,
        TimetableExtraction,
    )
    from .services.inference_cache import clear_inference_cache
    from .services.calendar_export import (
        adjusted_end_time,
        create_ics,
        event_title,
        module_text,
    )
    from .services.timetable_extraction import (
        extract_with_ollama,
        validate_extraction,
    )
    from .services.teaching_weeks import compress_weeks, parse_week_text
else:

    from models import (
        ClassEntry,
        TimetableExtraction,
    )
    from services.inference_cache import clear_inference_cache
    from services.calendar_export import (
        adjusted_end_time,
        create_ics,
        event_title,
        module_text,
    )
    from services.timetable_extraction import (
        extract_with_ollama,
        validate_extraction,
    )
    from services.teaching_weeks import compress_weeks, parse_week_text


BASE_DIR = Path(__file__).resolve().parent.parent
APP_ICON_FILE = Path(__file__).resolve().parent / "images" / "CalGen.png"
APP_ICON_ICO_FILE = Path(__file__).resolve().parent / "images" / "CalGen.ico"
WINDOWS_APP_ID = "UL.CalendarCreator"

INPUT_DIR = BASE_DIR / "input"


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
        if sys.platform == "win32":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                WINDOWS_APP_ID
            )

        super().__init__()

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.configure(fg_color=BG)
        self.window_icon = PhotoImage(file=str(APP_ICON_FILE))
        self.iconphoto(True, self.window_icon)
        if sys.platform == "win32":
            self.iconbitmap(default=str(APP_ICON_ICO_FILE))
        icon_source = Image.open(APP_ICON_FILE)
        self.header_icon_image = ctk.CTkImage(
            light_image=icon_source,
            dark_image=icon_source,
            size=(80, 60),
        )

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
            image=self.header_icon_image,
            text="▣",
            width=72,
            height=72,
            corner_radius=18,
            fg_color=LIGHT_BLUE,
            text_color=BLUE,
            font=ctk.CTkFont(size=34, weight="bold"),
        )
        self.header_icon.configure(text="")
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
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirector = TextRedirector(self.ui_queue)

        try:
            sys.stdout = redirector
            sys.stderr = redirector

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
                model=HIDDEN_MODEL,
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
