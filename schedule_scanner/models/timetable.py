
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


SEMESTER_YEAR = 2026


class TeachingWeek(BaseModel):
    week: int = Field(ge=1, le=60)
    day: int = Field(ge=1, le=31)
    month: int = Field(ge=1, le=12)

    def as_date(self) -> date:
        result = date(SEMESTER_YEAR, self.month, self.day)

        if result.weekday() != 0:
            raise ValueError(
                f"Teaching week {self.week} does not begin "
                f"on a Monday: {result}"
            )

        return result


class ClassEntry(BaseModel):
    day: Literal[
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    start_time: str
    end_time: str
    module_code: str
    module_name: str
    class_type: str
    lecturer: str
    room: str
    weeks: list[int] = Field(min_length=1)

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        value = value.strip()
        datetime.strptime(value, "%H:%M")
        return value

    @field_validator("weeks")
    @classmethod
    def validate_weeks(cls, value: list[int]) -> list[int]:
        clean = sorted(set(value))

        if not clean:
            raise ValueError("At least one teaching week is required.")

        if min(clean) < 1 or max(clean) > 60:
            raise ValueError("Invalid teaching week number.")

        return clean


class TimetableExtraction(BaseModel):
    teaching_weeks: list[TeachingWeek]
    classes: list[ClassEntry]


class TeachingWeeksExtraction(BaseModel):
    teaching_weeks: list[TeachingWeek]


class ClassBlockRead(BaseModel):
    start_time: str
    end_time: str
    module_code: str
    class_type: str
    lecturer: str
    room: str
    week_text: str

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        value = value.strip()
        datetime.strptime(value, "%H:%M")
        return value

    @field_validator("week_text")
    @classmethod
    def validate_week_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Visible week text is required.")
        return value


class DetectedBlock(BaseModel):
    day: str
    x0: int
    y0: int
    x1: int
    y1: int
