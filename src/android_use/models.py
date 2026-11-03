from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["tree", "screenshot", "both"]
Observe = Literal["none", "tree", "screenshot", "both"]
Direction = Literal["up", "down", "left", "right"]
Button = Literal["home", "back", "recent", "enter", "volume_up", "volume_down"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Selector(StrictModel):
    text: str | None = None
    text_contains: str | None = None
    description: str | None = None
    description_contains: str | None = None
    resource_id: str | None = None
    class_name: str | None = None
    package: str | None = None
    enabled: bool | None = None
    clickable: bool | None = None
    focused: bool | None = None
    index: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def has_filter(self):
        if not self.model_dump(exclude_none=True).keys() - {"index"}:
            raise ValueError("Selector needs at least one filter besides index")
        return self


class Region(StrictModel):
    x: Annotated[int, Field(ge=0)]
    y: Annotated[int, Field(ge=0)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]


class ObserveArgs(StrictModel):
    mode: Mode = "tree"
    max_nodes: Annotated[int, Field(ge=1, le=500)] = 200


class TapArgs(StrictModel):
    selector: Selector | None = None
    x: Annotated[int, Field(ge=0)] | None = None
    y: Annotated[int, Field(ge=0)] | None = None
    observation_id: str | None = None
    observe: Observe = "none"

    @model_validator(mode="after")
    def target(self):
        if self.selector is not None:
            if self.x is not None or self.y is not None:
                raise ValueError("Pass selector OR x/y")
        elif self.x is None or self.y is None:
            raise ValueError("Pass selector OR both x and y")
        return self


class TypeArgs(StrictModel):
    text: Annotated[str, Field(max_length=10000)]
    selector: Selector | None = None
    replace: bool = True
    allow_newlines: bool = False
    verify: bool = False
    observe: Observe = "none"

    @model_validator(mode="after")
    def newlines(self):
        if not self.allow_newlines and ("\n" in self.text or "\r" in self.text):
            raise ValueError("Newlines require allow_newlines=true; they may submit in some apps")
        return self


class SwipeArgs(StrictModel):
    direction: Direction = "up"
    region: Region | None = None
    duration: Annotated[float, Field(ge=0.1, le=2)] = 0.4
    observation_id: str | None = None
    observe: Observe = "none"


class PressArgs(StrictModel):
    name: Button
    observe: Observe = "none"


class LaunchArgs(StrictModel):
    package: Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$")]
    observe: Observe = "none"


class WaitArgs(StrictModel):
    selector: Selector
    timeout_seconds: Annotated[float, Field(ge=0, le=20)] = 5.0


class FindArgs(StrictModel):
    selector: Selector
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class BatchStep(StrictModel):
    op: Literal["tap", "type_text", "swipe", "press_button", "launch_app", "observe", "wait"]
    args: dict = Field(default_factory=dict)


ARG_MODELS = {
    "tap": TapArgs,
    "type_text": TypeArgs,
    "swipe": SwipeArgs,
    "press_button": PressArgs,
    "launch_app": LaunchArgs,
    "observe": ObserveArgs,
    "wait": WaitArgs,
    "find": FindArgs,
}
