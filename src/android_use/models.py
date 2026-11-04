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


class WaitCondition(StrictModel):
    selector: Selector
    state: Literal["visible", "gone"] = "visible"
    timeout_seconds: Annotated[float, Field(ge=0, le=20)] = 5.0


class ActionArgs(StrictModel):
    wait_for: WaitCondition | None = None
    observe: Observe = "none"


class TapArgs(ActionArgs):
    selector: Selector | None = None
    x: Annotated[int, Field(ge=0)] | None = None
    y: Annotated[int, Field(ge=0)] | None = None
    observation_id: str | None = None

    @model_validator(mode="after")
    def target(self):
        if self.selector is not None:
            if self.x is not None or self.y is not None:
                raise ValueError("Pass selector OR x/y")
        elif self.x is None or self.y is None:
            raise ValueError("Pass selector OR both x and y")
        return self


class LongPressArgs(TapArgs):
    duration: Annotated[float, Field(ge=0.5, le=3)] = 0.8


class DoubleTapArgs(TapArgs):
    interval: Annotated[float, Field(ge=0.04, le=0.25)] = 0.08


class Point(StrictModel):
    x: Annotated[int, Field(ge=0)]
    y: Annotated[int, Field(ge=0)]


class DragArgs(ActionArgs):
    start: Point
    end: Point
    duration: Annotated[float, Field(ge=0.1, le=3)] = 0.6
    observation_id: str | None = None

    @model_validator(mode="after")
    def distinct_points(self):
        if self.start == self.end:
            raise ValueError("Drag needs distinct start/end points")
        return self


class TypeArgs(ActionArgs):
    text: Annotated[str, Field(max_length=10000)]
    selector: Selector | None = None
    replace: bool = True
    allow_newlines: bool = False
    verify: bool = False

    @model_validator(mode="after")
    def newlines(self):
        if not self.allow_newlines and ("\n" in self.text or "\r" in self.text):
            raise ValueError("Newlines require allow_newlines=true; they may submit in some apps")
        return self


class SwipeArgs(ActionArgs):
    direction: Direction = "up"
    region: Region | None = None
    duration: Annotated[float, Field(ge=0.1, le=2)] = 0.4
    observation_id: str | None = None


class ScrollUntilArgs(StrictModel):
    selector: Selector
    direction: Direction = "up"
    region: Region | None = None
    duration: Annotated[float, Field(ge=0.1, le=2)] = 0.4
    max_swipes: Annotated[int, Field(ge=0, le=20)] = 8
    timeout_seconds: Annotated[float, Field(ge=0, le=20)] = 20.0
    settle_seconds: Annotated[float, Field(ge=0.1, le=2)] = 0.4
    observe: Observe = "tree"


class PressArgs(ActionArgs):
    name: Button


class LaunchArgs(ActionArgs):
    package: Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$")]


class WaitArgs(WaitCondition):
    pass


class FindArgs(StrictModel):
    selector: Selector
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class ExecuteGoalArgs(StrictModel):
    goal: Annotated[str, Field(min_length=1, max_length=2000)]
    package: Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$")]
    texts: Annotated[list[Annotated[str, Field(max_length=2000)]], Field(max_length=8)] = Field(
        default_factory=list
    )
    actions: Annotated[
        list[Literal["tap", "long_press", "swipe", "type_text", "back", "wait"]],
        Field(min_length=1, max_length=6),
    ] = Field(default_factory=lambda: ["tap", "swipe", "type_text", "wait"])
    completion: Selector | None = None
    completion_state: Literal["visible", "gone"] = "visible"
    max_steps: Annotated[int, Field(ge=1, le=20)] = 8
    max_model_calls: Annotated[int, Field(ge=1, le=40)] = 16
    timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 20.0
    min_probability: Annotated[float, Field(ge=0, le=1)] = 0.65
    min_margin: Annotated[float, Field(ge=0, le=1)] = 0.1
    observe: Literal["none", "tree"] = "tree"
    input_macros: bool = True
    effect_timeout_seconds: Annotated[float, Field(ge=0.1, le=3)] = 1.0


class FieldQuestion(StrictModel):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
    instructions: Annotated[str, Field(min_length=1, max_length=500)]
    kind: Literal["money", "text", "choice"] = "money"
    options: Annotated[dict[str, Annotated[str, Field(max_length=200)]], Field(max_length=32)] = (
        Field(default_factory=dict)
    )

    @model_validator(mode="after")
    def valid_options(self):
        if (self.kind == "choice") != bool(self.options):
            raise ValueError("Only choice fields require options")
        if any(not key or len(key) > 40 or key == "NONE" for key in self.options):
            raise ValueError("Option IDs must be short and may not use reserved NONE")
        return self


class FieldSumCheck(StrictModel):
    total: str
    add: Annotated[list[str], Field(min_length=1, max_length=10)]
    subtract: Annotated[list[str], Field(max_length=10)] = Field(default_factory=list)


class ReadFieldsArgs(StrictModel):
    package: Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$")]
    fields: Annotated[list[FieldQuestion], Field(min_length=1, max_length=12)]
    # Explicit provenance: supplied OCR is never represented as a direct phone read.
    evidence_text: Annotated[str, Field(min_length=1, max_length=12000)] | None = None
    region: Region | None = None
    timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 20.0
    min_probability: Annotated[float, Field(ge=0, le=1)] = 0.65
    min_margin: Annotated[float, Field(ge=0, le=1)] = 0.1
    sum_check: FieldSumCheck | None = None

    @model_validator(mode="after")
    def unique_fields(self):
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("Field names must be unique")
        if self.sum_check:
            money = {f.name for f in self.fields if f.kind == "money"}
            names = [self.sum_check.total, *self.sum_check.add, *self.sum_check.subtract]
            if not set(names) <= money or len(names) != len(set(names)):
                raise ValueError("Sum check must refer to distinct requested money fields")
        return self


class BatchStep(StrictModel):
    op: Literal[
        "tap",
        "long_press",
        "double_tap",
        "drag",
        "type_text",
        "swipe",
        "scroll_until",
        "press_button",
        "launch_app",
        "observe",
        "wait",
    ]
    args: dict = Field(default_factory=dict)


ARG_MODELS = {
    "read_fields": ReadFieldsArgs,
    "execute_goal": ExecuteGoalArgs,
    "tap": TapArgs,
    "long_press": LongPressArgs,
    "double_tap": DoubleTapArgs,
    "drag": DragArgs,
    "scroll_until": ScrollUntilArgs,
    "type_text": TypeArgs,
    "swipe": SwipeArgs,
    "press_button": PressArgs,
    "launch_app": LaunchArgs,
    "observe": ObserveArgs,
    "wait": WaitArgs,
    "find": FindArgs,
}
