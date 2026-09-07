import ctypes
from datetime import datetime
from pathlib import Path
import shutil
import sys
import tempfile
import webbrowser

import customtkinter as ctk
from PIL import Image
from pydantic import ValidationError
from tkinter import PhotoImage, filedialog, messagebox

try:
    from ..models import TimetableExtraction
    from ..services.calendar_export import create_ics
    from ..services.inference_cache import clear_inference_cache
except ImportError:
    from models import TimetableExtraction
    from services.calendar_export import create_ics
    from services.inference_cache import clear_inference_cache

from .extraction_controller import ExtractionController
from .review_view import ReviewView
from .theme import (
    APP_ICON_FILE,
    APP_ICON_ICO_FILE,
    APP_TITLE,
    BG,
    BLUE,
    BLUE_HOVER,
    BORDER,
    CARD,
    HIDDEN_MODEL,
    LIGHT_BLUE,
    MUTED,
    TEXT,
    WINDOWS_APP_ID,
)




class ScheduleScannerApp(ctk.CTk):
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
        self.extraction_controller = ExtractionController(HIDDEN_MODEL)
        self.extraction_controller.preload()
        self.review_view: ReviewView | None = None
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
            text="Clear Cache",
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
        if self.extraction_controller.running:
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
        self.extraction_controller.reset()
        self.timer_text.set("Time: 0.0s")
        self.status_text.set(
            "Cache cleared. Create the timetable to run a fresh test."
        )
        self._show_main_view()
        messagebox.showinfo(APP_TITLE, "Cache cleared.")





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
            messagebox.showerror(APP_TITLE, "Create the timetable first.")
            return

        self._clear_content()
        self.header_title.configure(text="Review Timetable")
        self.header_subtitle.configure(
            text="Edit any detected class details below before downloading your calendar."
        )
        self.review_view = ReviewView(
            self.content_host,
            self.footer,
            self.extraction,
            self._show_main_view,
            self._apply_review_changes,
        )

    def _apply_review_changes(
        self,
        edited: TimetableExtraction,
    ) -> None:
        self.extraction = edited
        self.generated_ics = None
        self.status_text.set(
            f"Changes saved — {len(edited.classes)} classes ready."
        )
        self._show_main_view()


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
        self.extraction_controller.reset()
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
        self.extraction_controller.start_timer()
        self.timer_text.set("Time: 0.0s")
        self.after(0, self._tick_timer)

    def _tick_timer(self) -> None:
        if not self.extraction_controller.running:
            return

        self.timer_text.set(
            f"Time: {self.extraction_controller.elapsed:.1f}s"
        )


        self.after(100, self._tick_timer)

    def _stop_timer(self) -> float:
        elapsed = self.extraction_controller.stop_timer()
        self.timer_text.set(f"Time: {elapsed:.1f}s")
        return elapsed





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

        if (
            self.extraction is not None
            and self.extraction_controller.matches(timetable, weeks)
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
        self.extraction_controller.start(timetable, weeks)
        self.after(0, self._tick_timer)







    def _drain_queue(self) -> None:
        for kind, payload in self.extraction_controller.drain_messages():
            if kind == "engine_log":
                continue

            if kind == "success":
                elapsed = self._stop_timer()
                self.extraction = payload
                try:
                    timetable = Path(self.timetable_path.get().strip())
                    weeks = Path(self.weeks_path.get().strip())
                    self.extraction_controller.remember(timetable, weeks)
                except OSError:
                    self.extraction_controller.reset()

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
                self.status_text.set("Could not create timetable.")
                messagebox.showerror("Could not create timetable", payload)

        self.after(100, self._drain_queue)


    def _build_ics(self) -> Path:
        if self.extraction is None:
            raise RuntimeError(
                "Create the timetable first."
            )

        generated_dir = (
            Path(tempfile.gettempdir())
            / "ScheduleScanner"
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
