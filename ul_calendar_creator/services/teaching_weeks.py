"""Parse and format teaching-week number ranges."""

import re


def parse_week_text(
    week_text: str,
    valid_week_numbers: set[int] | None = None,
) -> list[int]:
    """Convert literal timetable week text into exact week numbers."""
    original = week_text.strip()

    text = original.casefold()
    text = (
        text.replace("weeks", "")
        .replace("week", "")
        .replace("wks", "")
        .replace("wk", "")
        .replace(":", "")
        .replace(";", ",")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )

    text = re.sub(r"[^0-9,\-\s]", "", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", ",", text)
    text = re.sub(r",+", ",", text).strip(",")

    if not text:
        raise ValueError(
            f"Could not parse teaching weeks from {original!r}."
        )

    weeks: list[int] = []

    for part in text.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            pieces = [piece for piece in part.split("-") if piece]
            if len(pieces) != 2:
                raise ValueError(
                    f"Ambiguous week range {part!r} in {original!r}."
                )

            start = int(pieces[0])
            end = int(pieces[1])

            if start > end:
                raise ValueError(
                    f"Reversed week range {part!r} in {original!r}."
                )

            weeks.extend(range(start, end + 1))
        else:
            weeks.append(int(part))

    weeks = sorted(set(weeks))

    if not weeks:
        raise ValueError(f"No teaching weeks found in {original!r}.")

    if min(weeks) < 1 or max(weeks) > 60:
        raise ValueError(
            f"Invalid teaching week in {original!r}: {weeks}"
        )

    if valid_week_numbers is not None:
        unknown = sorted(set(weeks) - valid_week_numbers)
        if unknown:
            raise ValueError(
                f"Week text {original!r} contains weeks not present "
                f"in the teaching-weeks screenshot: {unknown}"
            )

    return weeks


def compress_weeks(weeks: list[int]) -> str:
    """Format week numbers as compact, comma-separated ranges."""
    weeks = sorted(set(weeks))

    if not weeks:
        return ""

    parts: list[str] = []
    start = weeks[0]
    previous = weeks[0]

    for week in weeks[1:]:
        if week == previous + 1:
            previous = week
            continue

        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = week
        previous = week

    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(parts)
