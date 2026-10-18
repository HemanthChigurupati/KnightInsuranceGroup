from pydantic import BaseModel, Field


class OverrideRequest(BaseModel):
    value: str | int | float | bool = Field(description="Corrected value; normalised like extracted values")
    note: str = Field(default="", max_length=500, description="Why the value was corrected")
