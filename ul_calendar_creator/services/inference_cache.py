
from pathlib import Path
import shutil
import tempfile


INFERENCE_CACHE_DIR = (
    Path(tempfile.gettempdir())
    / "ULCalendarCreator"
    / "inference-cache"
)


def clear_inference_cache() -> None:
    try:
        shutil.rmtree(INFERENCE_CACHE_DIR)
    except FileNotFoundError:
        pass
