"""面向中英文电影剧本格式的确定性场景切分器。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from movie_breakdown.domain.source import (
    NormalizedDocument,
    Scene,
    SceneFormatProfile,
    Screenplay,
    SourceLine,
    SourceSpan,
)
from movie_breakdown.infrastructure.fingerprint import hash_text

_NUMBERED_PUNCTUATION = re.compile(r"^\s*(?:第)?(?P<number>\d{1,4})[、.．]\s*(?P<title>.*)$")
_NUMBERED_SCENE = re.compile(r"^\s*(?:第)?(?P<number>\d{1,4})\s*场(?:[、.．:：\s]|$)")
_TIME_TOKEN = r"(?:雨夜|雨晨|清晨|凌晨|早晨|晨|日|夜|黄昏|黎明|傍晚|午后)"
_NUMBERED_META = re.compile(
    rf"^\s*(?P<number>\d{{1,4}})\s*{_TIME_TOKEN}(?:\s+(?:内|外)(?:\s|$)|\s*$)"
)
_UNNUMBERED_META = re.compile(rf"^\s*{_TIME_TOKEN}\s+(?:内|外)(?:\s+\S.*)?$")
_TIME_ONLY = re.compile(rf"^\s*{_TIME_TOKEN}\s*$")
_PROLOGUE_HEADING = re.compile(rf"^\s*序(?:场)?[、.．:：\s].*{_TIME_TOKEN}[-/\s]+(?:内|外)(?:\s|$)")
_STANDARD_HEADING = re.compile(r"^\s*(?:INT|EXT|INT\.?/EXT|内景|外景)[.．:： /]", re.IGNORECASE)
_FIELD_PATTERN = re.compile(r"^\s*(场景|时间|人物)\s*[:：]\s*(.*)$")
_MARKDOWN_HEADING = re.compile(r"^\s*#{2,6}\s+(?P<title>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class _Start:
    index: int
    inline_title: str


class UnsafeScenePatternError(ValueError):
    """模型生成的场景正则未通过本地安全约束。"""


def split_scenes(
    document: NormalizedDocument,
    profile: SceneFormatProfile | None = None,
) -> Screenplay:
    """根据稳定规则把规范化文档切分为顺序场景。

    Args:
        document: 保留行号和页码映射的规范化文档。
        profile: 已通过 Pydantic 校验的模型格式画像；省略时使用内置规则。

    Returns:
        包含顺序场景及各自内容指纹的剧本。
    """
    starts = (
        _find_custom_starts(document.lines, profile)
        if profile is not None
        else _find_starts(document.lines)
    )
    split_method = "model" if profile is not None else "builtin"
    if not starts:
        starts = [_Start(index=0, inline_title=document.title)]
        split_method = "fallback"

    scenes: list[Scene] = []
    for ordinal, start in enumerate(starts, start=1):
        end_index = starts[ordinal].index if ordinal < len(starts) else len(document.lines)
        scene_lines = document.lines[start.index : end_index]
        if not any(line.text.strip() for line in scene_lines):
            continue
        scenes.append(_build_scene(ordinal, start, scene_lines))

    return Screenplay(
        title=document.title,
        source_fingerprint=document.source.fingerprint,
        scenes=scenes,
        split_method=split_method,
        format_profile=profile if split_method == "model" else None,
    )


def split_is_reasonable(screenplay: Screenplay, document: NormalizedDocument) -> bool:
    """判断场景数量和平均长度是否足以信任当前切分。

    Args:
        screenplay: 待评估的场景切分结果。
        document: 对应的完整规范化文档。

    Returns:
        当前结果是否可直接进入逐场分析。
    """
    scene_count = len(screenplay.scenes)
    if len(document.lines) <= 80:
        return scene_count >= 1 and screenplay.split_method != "fallback"
    if scene_count < 2 or screenplay.split_method == "fallback":
        return False
    return len(document.lines) / scene_count <= 250


def _find_custom_starts(
    lines: list[SourceLine],
    profile: SceneFormatProfile,
) -> list[_Start]:
    """验证并应用模型生成的单行正则。"""
    pattern = _compile_safe_pattern(profile.scene_start_regex)
    if not all(pattern.match(example[:500]) for example in profile.heading_examples):
        raise UnsafeScenePatternError("场景正则不能覆盖模型给出的标题示例。")
    starts: list[_Start] = []
    for index, line in enumerate(lines):
        match = pattern.match(line.text[:500])
        if match:
            title = match.groupdict().get("title") or line.text.strip()
            starts.append(_Start(index=index, inline_title=title.strip()))
    return _deduplicate_starts(starts)


def _compile_safe_pattern(value: str) -> re.Pattern[str]:
    """拒绝易失控语法并编译行首锚定正则。"""
    if len(value) > 300 or not value.lstrip().startswith("^"):
        raise UnsafeScenePatternError("场景正则必须以 ^ 开头且不超过 300 个字符。")
    forbidden = (r"\1", r"\2", "(?=", "(?!", "(?<=", "(?<!", "(?P=", "(?R", "\\n")
    if any(token in value for token in forbidden):
        raise UnsafeScenePatternError("场景正则包含禁止的回溯引用、环视或跨行语法。")
    try:
        pattern = re.compile(value, re.IGNORECASE)
    except re.error as error:
        raise UnsafeScenePatternError(f"场景正则无法编译：{error}") from error
    if pattern.groups != len(pattern.groupindex) or set(pattern.groupindex) - {"title"}:
        raise UnsafeScenePatternError("场景正则只能包含可选的 title 命名分组。")
    return pattern


def _find_starts(lines: list[SourceLine]) -> list[_Start]:
    starts: list[_Start] = []
    for index, line in enumerate(lines):
        raw_text = line.text.strip()
        markdown = _MARKDOWN_HEADING.match(raw_text)
        text = markdown.group("title").strip() if markdown else raw_text
        punctuated = _NUMBERED_PUNCTUATION.match(text)
        if punctuated and _plausible_numbered_heading(lines, index, punctuated.group("title")):
            title = punctuated.group("title").strip()
            if title:
                starts.append(_Start(index, title))
            continue
        if (
            _NUMBERED_SCENE.match(text)
            or _NUMBERED_META.match(text)
            or _STANDARD_HEADING.match(text)
        ):
            starts.append(_Start(index, text))
            continue
        field_start = _scene_field_start(lines, index)
        if field_start is not None:
            starts.append(field_start)
            continue
        fragmented = _fragmented_meta_title(lines, index)
        if fragmented is not None:
            starts.append(_Start(index, fragmented))
            continue
        if _UNNUMBERED_META.match(text) or _PROLOGUE_HEADING.match(text):
            starts.append(_Start(index, text))
    return _deduplicate_starts(starts)


def _scene_field_start(lines: list[SourceLine], index: int) -> _Start | None:
    """把带时间或人物字段的 `场景：` 行识别为结构化场景起点。"""
    match = _FIELD_PATTERN.match(lines[index].text.strip())
    if match is None or match.group(1) != "场景":
        return None
    following = [line.text.strip() for line in lines[index + 1 : index + 8] if line.text.strip()]
    if not any(
        (field := _FIELD_PATTERN.match(text)) and field.group(1) in {"时间", "人物"}
        for text in following
    ):
        return None
    previous = _previous_nonblank(lines, index)
    if previous is not None:
        previous_index, previous_text = previous
        markdown = _MARKDOWN_HEADING.match(previous_text)
        candidate = markdown.group("title").strip() if markdown else previous_text
        numbered = _NUMBERED_PUNCTUATION.match(candidate)
        if numbered and not numbered.group("title").strip():
            return _Start(previous_index, match.group(2).strip())
    return _Start(index, match.group(2).strip())


def _previous_nonblank(lines: list[SourceLine], index: int) -> tuple[int, str] | None:
    """返回当前位置之前最近的非空行索引和文本。"""
    for previous_index in range(index - 1, max(-1, index - 5), -1):
        text = lines[previous_index].text.strip()
        if text:
            return previous_index, text
    return None


def _fragmented_meta_title(lines: list[SourceLine], index: int) -> str | None:
    """识别 PDF layout 抽取后分散在三行的时间、内外景和地点。"""
    text = lines[index].text.strip()
    if _TIME_ONLY.match(text) is None:
        return None
    following = [line.text.strip() for line in lines[index + 1 : index + 8] if line.text.strip()]
    if len(following) < 2 or following[0] not in {"内", "外", "内外", "外内"}:
        return None
    location = following[1]
    if len(location) > 100:
        return None
    return f"{text} {following[0]} {location}"


def _plausible_numbered_heading(lines: list[SourceLine], index: int, title: str) -> bool:
    if title:
        return len(title) <= 100 and not re.match(r"^\d+[.．]\d+", title)
    following = lines[index + 1 : index + 4]
    return any(_FIELD_PATTERN.match(line.text) for line in following)


def _deduplicate_starts(starts: list[_Start]) -> list[_Start]:
    result: list[_Start] = []
    for start in starts:
        if not result or start.index - result[-1].index > 1:
            result.append(start)
    return result


def _build_scene(ordinal: int, start: _Start, lines: list[SourceLine]) -> Scene:
    fields: dict[str, str] = {}
    for line in lines[:6]:
        match = _FIELD_PATTERN.match(line.text)
        if match:
            fields[match.group(1)] = match.group(2).strip()

    heading = fields.get("场景") or _expanded_heading(start.inline_title, lines)
    time_hint = fields.get("时间") or _find_token(heading, ("日", "夜", "晨", "黄昏", "傍晚"))
    interior = _find_token(heading, ("内景", "外景", "内", "外", "INT", "EXT"))
    characters = _parse_characters(fields.get("人物", ""))
    text = "\n".join(line.text for line in lines).strip()
    first_line, last_line = lines[0], lines[-1]
    return Scene(
        id=f"scene-{ordinal:04d}",
        ordinal=ordinal,
        heading=heading or f"场景 {ordinal}",
        location_hint=fields.get("场景"),
        time_hint=time_hint,
        interior_exterior_hint=interior,
        character_hints=characters,
        text=text,
        source_span=SourceSpan(
            line_start=first_line.number,
            line_end=last_line.number,
            page_start=first_line.page_number,
            page_end=last_line.page_number,
        ),
        content_fingerprint=hash_text(text),
    )


def _expanded_heading(inline_title: str, lines: list[SourceLine]) -> str:
    """为 PDF 中被拆成多行的编号、时间、内外景和地点重建标题。"""
    meaningful = [line.text.strip() for line in lines[:10] if line.text.strip()]
    if (
        meaningful
        and _NUMBERED_META.match(meaningful[0])
        and len(meaningful) >= 3
        and meaningful[1] in {"内", "外", "内外", "外内"}
        and len(meaningful[2]) <= 100
    ):
        return " ".join(meaningful[:3])
    return inline_title or lines[0].text.strip()


def _find_token(text: str, choices: tuple[str, ...]) -> str | None:
    upper = text.upper()
    return next((choice for choice in choices if choice.upper() in upper), None)


def _parse_characters(value: str) -> list[str]:
    if not value:
        return []
    raw_names = re.split(r"[、,，;；\s]+", value)
    return [name.strip("（）()等") for name in raw_names if name.strip("（）()等")]
