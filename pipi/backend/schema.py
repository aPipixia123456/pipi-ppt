from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Element(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(max_length=100)
    type: Literal["text", "shape", "image", "table", "chart"]
    x: float = Field(ge=-2560, le=1280)
    y: float = Field(ge=-1440, le=720)
    w: float = Field(gt=0, le=3840)
    h: float = Field(gt=0, le=2160)
    text: str = Field(default="", max_length=6000)
    font: str = Field(default="Noto Sans CJK SC", max_length=100)
    font_size: float = Field(default=24, ge=8, le=150)
    line_height: float = Field(default=1.25, ge=0.8, le=2.5)
    align: Literal["left", "center", "right", "justify"] = "left"
    valign: Literal["top", "middle", "bottom"] = "top"
    color: str = Field(default="#172033", pattern=r"^#[0-9a-fA-F]{6}$")
    fill: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    bold: bool = False
    editable: bool = True
    max_chars: int = Field(default=300, ge=1, le=6000)
    asset_id: str | None = Field(default=None, max_length=36)
    builtin_asset: str | None = Field(default=None, max_length=256)
    image_fit: Literal["contain", "fill", "cover"] = "contain"
    shape: Literal["rect", "ellipse", "line"] = "rect"
    rows: list[list[str]] = Field(default_factory=list, max_length=25)
    labels: list[str] = Field(default_factory=list, max_length=30)
    values: list[float] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def bounds(self):
        # Decorative template images may intentionally extend beyond the page;
        # both the web viewport and presentation slide clip these identically.
        if self.type == "image" and not self.editable:
            if self.x + self.w <= 0 or self.y + self.h <= 0:
                raise ValueError("Image outside slide")
        elif self.x < 0 or self.y < 0 or self.x + self.w > 1281 or self.y + self.h > 721:
            raise ValueError("Element outside slide")
        if any(len(row) > 12 or any(len(cell) > 300 for cell in row) for row in self.rows):
            raise ValueError("Table too large")
        if self.type == "chart" and (
            len(self.labels) != len(self.values) or any(abs(v) > 1e12 for v in self.values)
        ):
            raise ValueError("Invalid chart series")
        return self


class Slide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=100)
    name: str = Field(max_length=200)
    background: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    elements: list[Element] = Field(max_length=200)
    notes: str = Field(default="", max_length=10000)


class CreateDeck(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=20000)
    template_id: str = Field(max_length=36)
    slide_count: int = Field(default=10, ge=5, le=30)
    text_model: str = Field(min_length=1, max_length=160)
    image_model: str = Field(default="", max_length=160)
    images: bool = True
    document_ids: list[str] = Field(default_factory=list, max_length=5)


class UpdateDeck(BaseModel):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    outline: list[str] = Field(default_factory=list, max_length=30)
    slides: list[Slide] = Field(default_factory=list, max_length=30)


class Generate(BaseModel):
    text_model: str = Field(min_length=1, max_length=160)
    image_model: str = Field(default="", max_length=160)
    images: bool = True


class Rewrite(Generate):
    index: int = Field(ge=0, le=29)
    instruction: str = Field(min_length=1, max_length=3000)


class AdminPolicy(BaseModel):
    enabled: bool
    text_models: list[str] = Field(max_length=30)
    image_models: list[str] = Field(max_length=30)
    generation_concurrency: int = Field(ge=1, le=32)
    export_concurrency: int = Field(ge=1, le=8)
    user_running: int = Field(ge=1, le=4)
    user_queued: int = Field(ge=1, le=20)
    upload_mb: int = Field(ge=1, le=20)
    storage_mb: int = Field(ge=20, le=10240)

    @model_validator(mode="after")
    def configured(self):
        if self.enabled and (not self.text_models or not self.image_models):
            raise ValueError("Configure validated text/vision and image models before enabling")
        if any(not m.strip() or len(m) > 160 for m in self.text_models + self.image_models):
            raise ValueError("Invalid model identifier")
        return self
