from pathlib import Path
import queue
import sys
import threading
import time

try:
    from ..services.ollama_client import client as ollama
except ImportError:
    from services.ollama_client import client as ollama

try:
    from ..services.timetable_extraction import (
        CLASS_NUM_CTX,
        OLLAMA_KEEP_ALIVE,
        extract_with_ollama,
    )
except ImportError:
    from services.timetable_extraction import (
        CLASS_NUM_CTX,
        OLLAMA_KEEP_ALIVE,
        extract_with_ollama,
    )


class TextRedirector:
    def __init__(self, message_queue: queue.Queue):
        self.message_queue = message_queue

    def write(self, value: str) -> None:
        if value and value.strip():
            self.message_queue.put(("engine_log", value.strip()))

    def flush(self) -> None:
        pass


class ExtractionController:
    def __init__(self, model: str) -> None:
        self.model = model
        self.messages: queue.Queue = queue.Queue()
        self._model_checked = False
        self._signature: tuple | None = None
        self._timer_started_at: float | None = None
        self._warmup_started = False
        self._warmup_complete = threading.Event()

    @property
    def running(self) -> bool:
        return self._timer_started_at is not None

    @property
    def elapsed(self) -> float:
        if self._timer_started_at is None:
            return 0.0
        return time.perf_counter() - self._timer_started_at

    def reset(self) -> None:
        self._signature = None
        self._timer_started_at = None

    def start_timer(self) -> None:
        self._timer_started_at = time.perf_counter()

    def stop_timer(self) -> float:
        elapsed = self.elapsed
        self._timer_started_at = None
        return elapsed

    @staticmethod
    def file_signature(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return str(path.resolve()), stat.st_size, stat.st_mtime_ns

    def matches(self, timetable: Path, weeks: Path) -> bool:
        return self._signature == (
            self.file_signature(timetable),
            self.file_signature(weeks),
        )

    def remember(self, timetable: Path, weeks: Path) -> None:
        self._signature = (
            self.file_signature(timetable),
            self.file_signature(weeks),
        )

    def start(self, timetable: Path, weeks: Path) -> None:
        self.start_timer()
        threading.Thread(
            target=self._worker,
            args=(timetable, weeks),
            daemon=True,
        ).start()

    def preload(self) -> None:
        if self._warmup_started:
            return
        self._warmup_started = True
        threading.Thread(
            target=self._preload_worker,
            daemon=True,
        ).start()

    def drain_messages(self) -> list[tuple[str, object]]:
        messages: list[tuple[str, object]] = []
        while True:
            try:
                messages.append(self.messages.get_nowait())
            except queue.Empty:
                return messages

    def _worker(self, timetable: Path, weeks: Path) -> None:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirector = TextRedirector(self.messages)
        try:
            sys.stdout = redirector
            sys.stderr = redirector
            if self._warmup_started:
                self._warmup_complete.wait()
            self._ensure_model_available()

            result = extract_with_ollama(
                timetable_image=timetable,
                weeks_image=weeks,
                model=self.model,
            )
            self.messages.put(("success", result))
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _preload_worker(self) -> None:
        try:
            self._ensure_model_available()
            ollama.generate(
                model=self.model,
                prompt="",
                keep_alive=OLLAMA_KEEP_ALIVE,
                options={"num_ctx": CLASS_NUM_CTX},
            )
        except Exception as exc:
            self.messages.put(("engine_log", f"Model warm-up failed: {exc}"))
        finally:
            self._warmup_complete.set()

    def _ensure_model_available(self) -> None:
        if self._model_checked:
            return
        installed = ollama.list()
        model_names = {
            getattr(item, "model", "")
            for item in installed.models
        }
        if not any(
            name == self.model
            or name.startswith(self.model + ":")
            for name in model_names
        ):
            raise RuntimeError(
                "The local timetable reader is not installed yet.\n\n"
                "Run this once in PowerShell:\n"
                f"ollama pull {self.model}"
            )
        self._model_checked = True
