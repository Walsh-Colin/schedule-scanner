from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
APP_ICON_FILE = PACKAGE_DIR / "images" / "CalGen.png"
APP_ICON_ICO_FILE = PACKAGE_DIR / "images" / "CalGen.ico"
WINDOWS_APP_ID = "ScheduleScanner.Desktop"

APP_TITLE = "Schedule Scanner"
HIDDEN_MODEL = "qwen2.5vl:3b"

BG = "#f5f7fb"
CARD = "#ffffff"
BORDER = "#d9e1ec"
TEXT = "#10213a"
MUTED = "#66758a"
BLUE = "#1769e0"
BLUE_HOVER = "#0f58c2"
LIGHT_BLUE = "#eef5ff"

DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
DAY_INDEX = {day: index for index, day in enumerate(DAYS)}
