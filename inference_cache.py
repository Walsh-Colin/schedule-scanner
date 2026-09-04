"""Storage helpers for cached timetable inference results."""

from pathlib import Path
import shutil
import tempfile


INFERENCE_CACHE_DIR = (
    Path(tempfile.gettempdir())
    / "ULCalendarCreator"
    / "inference-cache"
)


def clear_inference_cache() -> None:
    """Delete all cached inference results, if the cache exists."""
    try:
        shutil.rmtree(INFERENCE_CACHE_DIR)
    except FileNotFoundError:
        pass
