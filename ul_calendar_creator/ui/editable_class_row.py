from collections.abc import Callable

import customtkinter as ctk
from .theme import DAYS

try:
    from ..models import ClassEntry
    from ..services.teaching_weeks import compress_weeks, parse_week_text
except ImportError:
    from models import ClassEntry
    from services.teaching_weeks import compress_weeks, parse_week_text

class EditableClassRow:
    def __init__(
        self,
        parent,
        cls: ClassEntry,
        on_delete: Callable[["EditableClassRow"], None],
        *,
        danger_color: str = "#b42318",
        danger_hover_color: str = "#8f1c13",
    ) -> None:
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
        for column, (variable, width) in enumerate(specs, start=1):
            ctk.CTkEntry(
                self.frame,
                textvariable=variable,
                width=width,
            ).grid(row=0, column=column, padx=3, pady=4)

        ctk.CTkButton(
            self.frame,
            text="×",
            width=34,
            fg_color=danger_color,
            hover_color=danger_hover_color,
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
            weeks=parse_week_text(self.weeks.get().strip(), valid_weeks),
        )
