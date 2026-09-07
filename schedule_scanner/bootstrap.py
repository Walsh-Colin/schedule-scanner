"""Packaged entry point. Configure Ollama before importing its Python client."""

import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.request


MODEL = "qwen2.5vl:3b"


def ensure_model(client, download, report):
    if any(item.model == MODEL for item in client.list().models):
        return True
    if not download:
        return False
    for item in client.pull(MODEL, stream=True):
        total = item.total or 0
        completed = item.completed or 0
        label = item.status or "Downloading…"
        if total:
            label += f" — {completed / 1024**2:.0f} / {total / 1024**2:.0f} MB"
        report(label, completed / total if total else 0)
    if not any(item.model == MODEL for item in client.list().models):
        raise RuntimeError("Download did not complete. Please retry.")
    return True


class LocalRuntime:
    def __init__(self):
        self.process = None
        self.log = None
        self.host = None
        self.cancelled = threading.Event()
        self.lock = threading.Lock()

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        if self.log is not None:
            self.log.close()
        runtime = Path(sys.executable).parent / "ollama" / "ollama.exe"
        if not runtime.is_file():
            raise RuntimeError("The bundled AI reader is missing. Please reinstall Schedule Scanner.")
        data = Path(os.environ["LOCALAPPDATA"]) / "ScheduleScanner"
        (data / "models").mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.host = f"http://127.0.0.1:{port}"
        os.environ["OLLAMA_HOST"] = self.host
        env = os.environ.copy()
        env["OLLAMA_MODELS"] = str(data / "models")
        env["OLLAMA_NO_CLOUD"] = "1"
        self.log = (data / "ollama.log").open("ab")
        with self.lock:
            if self.cancelled.is_set():
                raise RuntimeError("Setup cancelled.")
            self.process = subprocess.Popen(
                [str(runtime), "serve"], env=env, stdout=self.log,
                stderr=self.log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(120):
            if self.cancelled.is_set():
                raise RuntimeError("Setup cancelled.")
            if self.process.poll() is not None:
                raise RuntimeError(f"The AI reader stopped. See {data / 'ollama.log'}.")
            try:
                with opener.open(self.host + "/api/version", timeout=0.5):
                    return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError("The AI reader took too long to start. Please retry.")

    def stop(self):
        self.cancelled.set()
        with self.lock:
            self._stop_process()

    def _stop_process(self):
        if self.process is not None and self.process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, check=False,
            )
            self.process.wait(timeout=10)
        self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None


def prepare(runtime, setup_only=False):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Schedule Scanner — AI reader setup")
    root.geometry("580x330")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Set up your timetable reader", font=("Segoe UI", 18, "bold")).pack(anchor="w")
    ttk.Label(frame, text="A one-time model download requires internet and several GB of disk space.\nAfter setup, your screenshots are processed locally.\nYou can close this window and resume setup by opening the app again.", wraplength=520).pack(anchor="w", pady=16)
    status = tk.StringVar(value="Checking the local reader…")
    ttk.Label(frame, textvariable=status, wraplength=520).pack(anchor="w", pady=8)
    progress = ttk.Progressbar(frame, maximum=1)
    progress.pack(fill="x", pady=8)
    events = queue.Queue()
    ready = False
    busy = False

    def worker(download):
        try:
            runtime.start()
            import ollama
            client = ollama.Client(host=runtime.host, timeout=60, trust_env=False)
            if not ensure_model(client, download, lambda label, fraction: events.put(("progress", (label, fraction)))):
                events.put(("missing", None))
                return
            events.put(("ready", None))
        except Exception as exc:
            events.put(("error", str(exc)))

    def start(download=True):
        nonlocal busy
        if busy:
            return
        busy = True
        button.configure(state="disabled")
        status.set("Preparing download…" if download else "Starting the local reader…")
        threading.Thread(target=worker, args=(download,), daemon=True).start()

    def poll():
        nonlocal ready, busy
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "progress":
                    status.set(value[0])
                    progress["value"] = value[1]
                elif kind == "ready":
                    busy = False
                    ready = True
                    if not setup_only:
                        root.destroy()
                        return
                    status.set("Setup complete. You can now use Schedule Scanner.")
                    progress["value"] = 1
                    button.configure(text="Finish", state="normal", command=root.destroy)
                else:
                    busy = False
                    status.set("Ready to download the AI model." if kind == "missing" else f"Setup could not finish: {value}")
                    button.configure(text="Download model" if kind == "missing" else "Retry", state="normal")
        except queue.Empty:
            pass
        root.after(100, poll)

    button = ttk.Button(frame, text="Download model", command=start)
    button.pack(anchor="e", pady=12)
    start(False)
    root.after(100, poll)
    root.mainloop()
    return ready


def main():
    if "--self-test" in sys.argv:
        try:
            import tkinter
            import cv2
            import numpy
            from PIL import Image
            from zoneinfo import ZoneInfo
            from .ui.app import ScheduleScannerApp
            from .ui.theme import APP_ICON_FILE, APP_ICON_ICO_FILE
            tkinter.Tcl().eval("info patchlevel")
            ZoneInfo("Europe/Dublin")
            with Image.open(APP_ICON_FILE) as icon:
                icon.verify()
            if not APP_ICON_ICO_FILE.is_file():
                return 1
            return 0
        except Exception:
            return 1
    if not getattr(sys, "frozen", False):
        from .schedule_scanner import main as run_app
        run_app()
        return 0
    runtime = LocalRuntime()
    try:
        setup_only = "--setup" in sys.argv
        if not prepare(runtime, setup_only):
            return 1
        if not setup_only:
            from .schedule_scanner import main as run_app
            run_app()
        return 0
    finally:
        runtime.stop()
