from collections.abc import Callable

import customtkinter as ctk
from pydantic import ValidationError
from tkinter import messagebox

try:
    from ..models import TimetableExtraction
    from ..services.timetable_extraction import validate_extraction
except ImportError:
    from models import TimetableExtraction
    from services.timetable_extraction import validate_extraction

from .editable_class_row import EditableClassRow
from .theme import BLUE, BLUE_HOVER, BORDER, DAY_INDEX, TEXT


class ReviewView:
    def __init__(
        self,
        content_parent,
        footer_parent,
        extraction: TimetableExtraction,
        on_back: Callable[[], None],
        on_saved: Callable[[TimetableExtraction], None],
    ) -> None:
        self.extraction = extraction
        self.on_back = on_back
        self.on_saved = on_saved
        self.class_rows: list[EditableClassRow] = []

        self.frame = ctk.CTkFrame(
            content_parent,
            fg_color="transparent",
        )
        self.frame.grid(row=0, column=0, sticky="nsew")
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_rowconfigure(1, weight=1)

        header_row = ctk.CTkFrame(
            self.frame,
            fg_color="transparent",
        )
        header_row.grid(row=0, column=0, sticky="w", pady=(0, 4))
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
        for column, (label, width) in enumerate(headers):
            ctk.CTkLabel(
                header_row,
                text=label,
                text_color=TEXT,
                width=width,
                font=ctk.CTkFont(weight="bold"),
            ).grid(row=0, column=column, padx=3, pady=4)

        self.rows_frame = ctk.CTkScrollableFrame(
            self.frame,
            fg_color="transparent",
        )
        self.rows_frame.grid(row=1, column=0, sticky="nsew")

        classes = sorted(
            extraction.classes,
            key=lambda cls: (
                DAY_INDEX[cls.day],
                cls.start_time,
                cls.end_time,
            ),
        )
        for cls in classes:
            self.class_rows.append(
                EditableClassRow(
                    self.rows_frame,
                    cls,
                    self._delete_row,
                )
            )
        self._regrid_rows()

        footer_parent.grid_columnconfigure(0, weight=1)
        footer_parent.grid_columnconfigure(1, weight=0)
        footer_parent.grid_columnconfigure(2, weight=0)
        ctk.CTkButton(
            footer_parent,
            text="←  Back",
            width=140,
            height=52,
            fg_color="transparent",
            hover_color="#edf3fb",
            text_color=TEXT,
            border_width=1,
            border_color=BORDER,
            command=on_back,
        ).grid(row=0, column=1, padx=(0, 10))
        ctk.CTkButton(
            footer_parent,
            text="Save Changes",
            width=170,
            height=52,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._save,
        ).grid(row=0, column=2)

    def _delete_row(self, row: EditableClassRow) -> None:
        if row in self.class_rows:
            self.class_rows.remove(row)
            row.destroy()
            self._regrid_rows()

    def _regrid_rows(self) -> None:
        for index, row in enumerate(self.class_rows):
            row.grid(index)

    def _save(self) -> None:
        try:
            valid_weeks = {
                week.week
                for week in self.extraction.teaching_weeks
            }
            classes = [
                row.to_class_entry(valid_weeks)
                for row in self.class_rows
            ]
            if not classes:
                raise RuntimeError("There are no classes to save.")

            edited = TimetableExtraction(
                teaching_weeks=self.extraction.teaching_weeks,
                classes=classes,
            )
            issues = validate_extraction(edited)
            if issues:
                raise RuntimeError(
                    "Please fix these timetable details:\n\n"
                    + "\n".join(f"• {issue}" for issue in issues)
                )
            self.on_saved(edited)
        except (ValidationError, ValueError, RuntimeError) as exc:
            messagebox.showerror("Cannot save changes", str(exc))
