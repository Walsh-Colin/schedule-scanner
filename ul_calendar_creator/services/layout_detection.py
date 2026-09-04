"""Detect timetable columns and class blocks from screenshot geometry."""

from pathlib import Path

import cv2
import numpy as np

try:
    from ..models import DetectedBlock
except ImportError:
    # Support importing the service when the application file is run directly.
    from models import DetectedBlock


BASE_DIR = Path(__file__).resolve().parents[2]
LAYOUT_DEBUG_FILE = BASE_DIR / "output" / "layout_debug.png"
SAVE_LAYOUT_DEBUG = False

DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
DAY_INDEX = {day: index for index, day in enumerate(DAYS)}


def _group_consecutive(values: np.ndarray) -> list[tuple[int, int]]:
    if len(values) == 0:
        return []

    groups: list[tuple[int, int]] = []
    start = previous = int(values[0])

    for raw in values[1:]:
        value = int(raw)
        if value > previous + 1:
            groups.append((start, previous))
            start = value
        previous = value

    groups.append((start, previous))
    return groups


def detect_day_columns(image: np.ndarray) -> tuple[list[int], int, np.ndarray]:
    """Detect day boundaries, the header bottom, and grid-free text."""
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 225, 255, cv2.THRESH_BINARY_INV)

    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, max(30, height // 12)),
    )
    vertical_lines = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        vertical_kernel,
    )
    vertical_projection = (vertical_lines > 0).sum(axis=0)

    clean_boundaries: list[int] = []
    projection_fraction = 0.55
    for candidate_fraction in (0.55, 0.45, 0.35, 0.25):
        x_candidates = np.where(
            vertical_projection > height * candidate_fraction
        )[0]
        boundaries = [
            int(round((left + right) / 2))
            for left, right in _group_consecutive(x_candidates)
        ]

        candidate_boundaries: list[int] = []
        for x in boundaries:
            if (
                not candidate_boundaries
                or x - candidate_boundaries[-1] >= max(20, width // 50)
            ):
                candidate_boundaries.append(x)

        clean_boundaries = candidate_boundaries
        projection_fraction = candidate_fraction
        if len(clean_boundaries) >= 6:
            break

    if len(clean_boundaries) < 6:
        raise RuntimeError(
            "OpenCV could not detect enough timetable day columns. "
            f"Detected boundaries: {clean_boundaries} "
            f"(minimum line coverage tried: {projection_fraction:.0%})."
        )

    dark_fraction = (binary > 0).mean(axis=1)
    header_rows = np.where(dark_fraction > 0.60)[0]
    header_bottom = max(25, round(height * 0.04))
    for top, bottom in _group_consecutive(header_rows):
        if top < height * 0.15 and (bottom - top + 1) >= 8:
            header_bottom = bottom + 1
            break

    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(30, width // 18), 1),
    )
    horizontal_lines = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        horizontal_kernel,
    )
    grid = cv2.bitwise_or(vertical_lines, horizontal_lines)
    return clean_boundaries, header_bottom, cv2.subtract(binary, grid)


def detect_class_blocks(
    timetable_image: Path,
) -> tuple[np.ndarray, list[DetectedBlock]]:
    """Detect text groups in each day column using screenshot geometry."""
    image = cv2.imread(str(timetable_image))
    if image is None:
        raise RuntimeError(f"Could not open timetable image: {timetable_image}")

    height, width = image.shape[:2]
    boundaries, body_top, text_only = detect_day_columns(image)
    interval_count = min(len(boundaries) - 1, len(DAYS))
    if interval_count < 5:
        raise RuntimeError(
            f"Expected at least Monday-Friday columns; found {interval_count}."
        )

    blocks: list[DetectedBlock] = []
    group_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(7, round(width * 0.010)), max(9, round(height * 0.015))),
    )

    for day_index in range(interval_count):
        left = boundaries[day_index]
        right = boundaries[day_index + 1]
        if right - left < 40:
            continue

        inner_left = left + 3
        inner_right = right - 3
        column_mask = text_only[body_top:height, inner_left:inner_right]
        grouped = cv2.dilate(column_mask, group_kernel, iterations=1)
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            grouped,
            connectivity=8,
        )

        components: list[tuple[int, int, int, int, int]] = []
        min_area = max(180, round(width * height * 0.00022))
        min_width = max(18, round(width * 0.020))
        min_height = max(18, round(height * 0.025))

        for label in range(1, count):
            x, y, w, h, area = [int(value) for value in stats[label]]
            if area >= min_area and w >= min_width and h >= min_height:
                components.append((x, y, w, h, area))

        components.sort(key=lambda item: item[1])
        for x, y, w, h, _ in components:
            pad_x = max(7, round(width * 0.008))
            pad_y = max(6, round(height * 0.007))
            blocks.append(
                DetectedBlock(
                    day=DAYS[day_index],
                    x0=max(left + 1, inner_left + x - pad_x),
                    x1=min(right - 1, inner_left + x + w + pad_x),
                    y0=max(body_top, body_top + y - pad_y),
                    y1=min(height - 1, body_top + y + h + pad_y),
                )
            )

    blocks.sort(
        key=lambda block: (DAY_INDEX.get(block.day, 99), block.y0, block.x0)
    )
    return image, blocks


def save_layout_debug(
    image: np.ndarray,
    blocks: list[DetectedBlock],
) -> None:
    """Save an annotated block-detection image when debugging is enabled."""
    if not SAVE_LAYOUT_DEBUG:
        return

    LAYOUT_DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
    debug = image.copy()
    for index, block in enumerate(blocks, start=1):
        cv2.rectangle(
            debug,
            (block.x0, block.y0),
            (block.x1, block.y1),
            (0, 0, 255),
            2,
        )
        cv2.putText(
            debug,
            f"{index} {block.day}",
            (block.x0 + 2, max(18, block.y0 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(LAYOUT_DEBUG_FILE), debug)


def make_block_crop_bytes(image: np.ndarray, block: DetectedBlock) -> bytes:
    """Crop, upscale, and encode one detected class entirely in memory."""
    crop = image[block.y0:block.y1, block.x0:block.x1]
    if crop.size == 0:
        raise RuntimeError(f"Empty crop detected for {block.day}.")

    enlarged = cv2.resize(
        crop,
        None,
        fx=3,
        fy=3,
        interpolation=cv2.INTER_CUBIC,
    )
    ok, encoded = cv2.imencode(
        ".png",
        enlarged,
        [cv2.IMWRITE_PNG_COMPRESSION, 3],
    )
    if not ok:
        raise RuntimeError(f"Could not encode class crop for {block.day}.")
    return encoded.tobytes()
