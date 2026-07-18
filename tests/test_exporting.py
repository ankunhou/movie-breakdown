import json
from pathlib import Path

import pytest

from movie_breakdown.application.exporting import ExportService, InvalidExportError
from movie_breakdown.domain.character_biography import (
    BiographyClaimBasis,
    CharacterBiographyClaim,
)
from movie_breakdown.infrastructure.storage import ProjectStore
from tests.factories import make_breakdown


def test_export_json_and_markdown(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "project")
    breakdown = make_breakdown()

    paths = ExportService().export(store, breakdown, "all")

    payload = json.loads(Path(paths["json"]).read_text("utf-8"))
    markdown = Path(paths["markdown"]).read_text("utf-8")
    assert payload["structure"]["logline"] == "青年小王踏上离乡列车。"
    assert payload["dossiers"]["dossiers"][0]["character_id"] == "char-xiaowang"
    assert payload["biographies"]["biographies"][0]["character_id"] == "char-xiaowang"
    assert "## 三幕结构" in markdown
    assert "## 人物分级档案" in markdown
    assert "### 核心人物（1）" in markdown
    assert "## 核心人物完整小传" in markdown
    assert "分析推断" in markdown
    assert "推断依据" in markdown
    assert "剧本未提供" in markdown
    assert "scene-0003" in markdown


def test_markdown_keeps_reported_claim_separate_from_observed_fact(tmp_path: Path) -> None:
    breakdown = make_breakdown()
    claim = breakdown.biographies.biographies[0].claims[0]
    payload = claim.model_dump(mode="json")
    payload.update(
        basis=BiographyClaimBasis.REPORTED,
        attribution="母亲",
        rationale=None,
    )
    breakdown.biographies.biographies[0].claims = [CharacterBiographyClaim.model_validate(payload)]

    paths = ExportService().export(ProjectStore(tmp_path / "project"), breakdown, "markdown")
    markdown = Path(paths["markdown"]).read_text("utf-8")

    assert "#### 角色或文本转述" in markdown
    assert "信息来源：母亲" in markdown


def test_refuse_export_when_validation_failed(tmp_path: Path) -> None:
    breakdown = make_breakdown()
    breakdown.validation.valid = False

    with pytest.raises(InvalidExportError, match="校验未通过"):
        ExportService().export(ProjectStore(tmp_path / "project"), breakdown)


def test_export_filename_cannot_escape_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不能包含目录"):
        ProjectStore(tmp_path / "project").write_export("../result.json", "{}")
