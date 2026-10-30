"""Literal mission-time bindings, shared by model checks and guarded refinement.

This is a deliberately local grammar, not a general natural-language parser.
An unrelated quantity is never discovered by skipping arbitrary text.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from .nl_aliases import EXPLICIT_PARAMETER_ALIASES

NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_FACTORS = {
    "milliseconds": .001, "millisecond": .001, "msecs": .001, "msec": .001,
    "ms": .001, "毫秒": .001,
    "microseconds": .000001, "microsecond": .000001, "us": .000001,
    "µs": .000001, "μs": .000001, "微秒": .000001,
    "seconds": 1., "second": 1., "secs": 1., "sec": 1., "s": 1., "秒": 1.,
    "minutes": 60., "minute": 60., "mins": 60., "min": 60., "m": 60., "分钟": 60., "分": 60.,
    "hours": 3600., "hour": 3600., "hrs": 3600., "hr": 3600., "h": 3600., "小时": 3600., "时": 3600.,
}
_UNIT = "(?:" + "|".join(re.escape(unit) for unit in sorted(_FACTORS, key=len, reverse=True)) + ")"
_UNIT_END = r"(?![A-Za-z0-9_/^²³]|\s*[/^])"
_FIELDS = {
    "duration_s": r"simulation\.duration_s|duration_s|总时长|仿真时长|运行时长|持续时间|运行时间|仿真时间|时长|持续|仿真|运行|模拟|duration|run\s+time|simulation|simulate|run",
    "sample_s": r"simulation\.sample_s|sample_s|输出采样间隔|采样间隔|采样周期|记录周期|输出间隔|sampling\s+interval|sample\s+interval|sampling\s+period|sample\s+period|recording\s+period",
    "step_s": r"simulation\.solver\.step_s|simulation\.step_s|solver\.step_s|step_s|积分步长|仿真步长|计算步长|步长|integration\s+step|time\s+step|step\s+size|step",
}

def literal_label_pattern(alternatives: tuple[str, ...]) -> str:
    """Allow numeric adjacency for Chinese labels, keeping ASCII identifiers whole."""
    chinese = [item for item in alternatives if re.search(r"[\u3400-\u9fff]", item)]
    ascii_labels = [item for item in alternatives if item not in chinese]
    branches = []
    if chinese:
        branches.append(r"(?:" + "|".join(chinese) + r")(?![A-Za-z_])")
    if ascii_labels:
        branches.append(r"(?:" + "|".join(ascii_labels) + r")(?![A-Za-z0-9_])")
    return r"(?<![A-Za-z0-9_])(?:" + "|".join(branches) + ")"


_LABEL = literal_label_pattern(tuple(alias for pattern in _FIELDS.values() for alias in pattern.split("|")))
_SET = r"(?:改成|改为|更改为|修改为|调整为|调整至|设置为|设为|为|是|到|至|[:：=]|to|for|of|is|(?:set|change|adjust)(?:\s+to)?)"
_Q = rf"(?P<value>{NUMBER})\s*(?P<unit>{_UNIT}){_UNIT_END}"
_FORWARD = re.compile(rf"(?P<label>{_LABEL})\s*(?:{_SET}\s*)?{_Q}", re.I)
_RANGE = re.compile(rf"(?P<label>{_LABEL})\s*(?:从|from)\s*{NUMBER}\s*(?:{_UNIT}{_UNIT_END})?\s*(?:改成|改为|调整为|到|至|to)\s*{_Q}", re.I)
_REVERSE = re.compile(rf"(?<![A-Za-z0-9_.+\-第]){_Q}\s*(?:的)?(?P<labels>{_LABEL}(?:\s*(?:和|与|及|、|and)\s*{_LABEL})*)", re.I)
_UNITLESS = re.compile(rf"(?P<label>{_LABEL})\s*(?P<assign>{_SET})?\s*(?P<value>{NUMBER})\s*$", re.I)
_RAW_UNIT = r"(?:[A-Za-zµμ][A-Za-z0-9_/^²³+\-]*|[\u3400-\u9fff]+)"
_EVERY_SAMPLE = re.compile(rf"每\s*(?P<value>{NUMBER})\s*(?P<unit>{_RAW_UNIT})\s*(?:采样|记录)(?![A-Za-z0-9_率])", re.I)
_DURATION_MODIFIER = r"(?!(?:at|in|during|before|after|until|within|with|and|or|of|for|to|per|step|interval|period|sample|sampling)\b)[A-Za-z][A-Za-z-]*"
_ATTRIBUTIVE_DURATION = re.compile(
    rf"(?<![A-Za-z0-9_.+\-第])(?P<value>{NUMBER})\s*-\s*(?P<unit>{_RAW_UNIT})"
    rf"(?:\s+{_DURATION_MODIFIER}){{0,4}}\s+(?P<label>simulation)(?![A-Za-z0-9_])", re.I)
_NEGATIVE = re.compile(r"不要|不把|不将|不修改|不改变|不更改|别把|\b(?:do\s+not|don't|not)\b", re.I)
_KEEP = re.compile(r"保留|保持|不变|仍用|\b(?:keep|preserve|unchanged)\b", re.I)


@dataclass(frozen=True)
class TimeBindings:
    updates: dict[str, float]
    preserved: dict[str, float]

    @property
    def requirements(self) -> dict[str, float]:
        return {**self.preserved, **self.updates}


def _field(label: str) -> str:
    for name, pattern in _FIELDS.items():
        if re.fullmatch(pattern, label, re.I):
            return name
    raise ValueError(f"Unknown mission-time field: {label}")


def _global_label(clause: str, start: int, label: str) -> bool:
    if _field(label) == "duration_s":
        prefix = clause[:start]
        if re.search(r"(?:故障|退化|事件|fault|failure|event|degradation)\s*(?:的\s*)?$", prefix, re.I):
            return False
        if label == "持续" and not re.search(r"(?:仿真|模拟|运行|simulation|run)\s*$", prefix, re.I):
            return False
    # Subsystem-specific periods are parameters, not the mission integrator.
    for path, aliases in EXPLICIT_PARAMETER_ALIASES.items():
        if not path.startswith('parameters.values.'):
            continue
        for alias in (*aliases, path.rsplit('.', 1)[-1]):
            pattern = r'(?<![A-Za-z0-9_])' + re.escape(alias).replace(r'\ ', r'\s+') + r'(?![A-Za-z0-9_])'
            if any(match.start() <= start < match.end() for match in re.finditer(pattern, clause, re.I)):
                return False
    return not (label.lower() in {"步长", "step", "step size"}
                and re.search(r"(?:热控|环境|动力学|飞控|thermal\s+|dynamics\s+|fsw\s+|control\s+)$", clause[:start], re.I))


def parse_time_bindings(text: str) -> TimeBindings:
    latest = str(text or "").rsplit("本轮补充指令：", 1)[-1]
    updates: dict[str, float] = {}
    preserved: dict[str, float] = {}
    for clause in re.split(r"[,，;；。\n]|并(?:且)?|(?<!不)但(?:是)?|\b(?:and|but)\b", latest, flags=re.I):
        if _NEGATIVE.search(clause):
            continue
        keep = bool(_KEEP.search(clause))
        covered: list[tuple[int, int]] = []

        def bind(label: str, start: int, raw: str, unit: str | None) -> None:
            if not _global_label(clause, start, label):
                return
            key = _field(label)
            if unit and unit.lower() not in _FACTORS:
                raise ValueError(f"Expected a complete time unit for {key}; got {unit!r}")
            value = float(raw) * _FACTORS[unit.lower()] if unit else float(raw)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a finite positive time, got {raw} {unit or 's'}")
            for existing in (updates, preserved):
                if key in existing and not math.isclose(existing[key], value, rel_tol=1e-12, abs_tol=0.):
                    raise ValueError(f"Conflicting explicit values for {key}")
            (preserved if keep else updates)[key] = value

        for match in _EVERY_SAMPLE.finditer(clause):
            bind("sample_s", match.start(), match["value"], match["unit"])
            covered.append(match.span())
        for match in _ATTRIBUTIVE_DURATION.finditer(clause):
            bind(match["label"], match.start("label"), match["value"], match["unit"])
            covered.append(match.span())
        for pattern in (_RANGE, _FORWARD):
            for match in pattern.finditer(clause):
                if any(start <= match.start('label') < end for start, end in covered):
                    continue
                bind(match['label'], match.start('label'), match['value'], match['unit'])
                covered.append(match.span())
        for match in _REVERSE.finditer(clause):
            for label in re.finditer(_LABEL, match['labels'], re.I):
                start = match.start('labels') + label.start()
                if not any(left <= start < right for left, right in covered):
                    bind(label.group(), start, match['value'], match['unit'])
            covered.append(match.span())
        for match in _UNITLESS.finditer(clause):
            if any(start <= match.start('label') < end for start, end in covered):
                continue
            if match['assign'] or match['label'].lower().endswith('_s'):
                bind(match['label'], match.start('label'), match['value'], None)
                covered.append(match.span())
        # A directly attached but non-time quantity must not silently become seconds.
        for label in re.finditer(_LABEL, clause, re.I):
            if not _global_label(clause, label.start(), label.group()):
                continue
            if any(start <= label.start() < end for start, end in covered):
                continue
            tail = clause[label.end():]
            tail = re.sub(rf"^\s*(?:{_SET})?\s*", "", tail, count=1, flags=re.I)
            if re.match(rf"(?:从\s*|from\s+)?{NUMBER}", tail, re.I):
                raise ValueError(f"Expected a complete time quantity for {_field(label.group())}; got {tail.strip()!r}")
            prefix = clause[:label.start()]
            if re.search(rf"{NUMBER}\s*[A-Za-zµμ][A-Za-z0-9_/*^²³-]*\s*$", prefix):
                raise ValueError(f"Expected a time unit before {label.group()!r}")
    return TimeBindings(updates, preserved)
