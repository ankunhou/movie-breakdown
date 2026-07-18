from pathlib import Path

from movie_breakdown.infrastructure.parsers import read_and_normalize
from movie_breakdown.infrastructure.scene_splitter import split_scenes


def _write_script(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "示例.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_public_sample_screenplay_is_locally_parseable() -> None:
    sample = Path(__file__).parents[1] / "examples" / "sample-screenplay.txt"

    scenes = split_scenes(read_and_normalize(sample)).scenes

    assert len(scenes) == 4
    assert [scene.heading for scene in scenes] == [
        "旧港灯塔控制室",
        "海湾栈桥",
        "INT. WEATHER STATION - MORNING",
        "EXT. HARBOR SQUARE - DAY",
    ]


def test_split_chinese_metadata_scenes(tmp_path: Path) -> None:
    path = _write_script(
        tmp_path,
        """电影剧本
人物表
1、
场景：雪山顶上
时间：日/夜
人物：小王 老李
△二人等待天亮。
2、
场景：前沿阵地
时间：日
人物：小王
小王：准备战斗！
""",
    )

    screenplay = split_scenes(read_and_normalize(path))

    assert [scene.id for scene in screenplay.scenes] == ["scene-0001", "scene-0002"]
    assert screenplay.scenes[0].heading == "雪山顶上"
    assert screenplay.scenes[0].time_hint == "日/夜"
    assert screenplay.scenes[0].character_hints == ["小王", "老李"]
    assert screenplay.scenes[1].source_span.line_start == 8


def test_split_common_inline_headings(tmp_path: Path) -> None:
    path = _write_script(
        tmp_path,
        """片名
1. 序场、场馆日内
内容一
2 日 外 锦绣家园
内容二
3、白鹿原麦地 日-外
内容三
""",
    )

    scenes = split_scenes(read_and_normalize(path)).scenes

    assert [scene.heading for scene in scenes] == [
        "序场、场馆日内",
        "2 日 外 锦绣家园",
        "白鹿原麦地 日-外",
    ]


def test_fallback_to_single_scene(tmp_path: Path) -> None:
    path = _write_script(tmp_path, "一部没有场次标题但拥有足够正文长度的短剧本内容。")

    scenes = split_scenes(read_and_normalize(path)).scenes

    assert len(scenes) == 1
    assert scenes[0].id == "scene-0001"


def test_split_markdown_and_extended_time_headings(tmp_path: Path) -> None:
    path = _write_script(
        tmp_path,
        """片名
## 序、旧宅 夜-内
序场正文。
## 1 雨夜 外 医院门口
第一场正文。
## 清晨 内 值班室
无编号场正文。
## 2 日 内 病房
第二场正文。
""",
    )

    scenes = split_scenes(read_and_normalize(path)).scenes

    assert [scene.heading for scene in scenes] == [
        "序、旧宅 夜-内",
        "1 雨夜 外 医院门口",
        "清晨 内 值班室",
        "2 日 内 病房",
    ]


def test_split_multiple_scene_fields_under_group_heading(tmp_path: Path) -> None:
    path = _write_script(
        tmp_path,
        """片名
## 54-58（若干小场）
场景：战场
时间：日
人物：小王
动作一。
场景：医院
时间：夜
人物：老李
动作二。
""",
    )

    scenes = split_scenes(read_and_normalize(path)).scenes

    assert [scene.heading for scene in scenes] == ["战场", "医院"]


def test_split_fragmented_pdf_style_heading(tmp_path: Path) -> None:
    path = _write_script(
        tmp_path,
        """片名
1日
外
锦绣家园
第一场正文。
2雨晨
内
办公室
第二场正文。
""",
    )

    scenes = split_scenes(read_and_normalize(path)).scenes

    assert [scene.heading for scene in scenes] == ["1日 外 锦绣家园", "2雨晨 内 办公室"]
