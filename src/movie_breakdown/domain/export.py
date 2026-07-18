"""稳定 JSON 和 Markdown 导出使用的聚合模型。"""

from __future__ import annotations

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.character_biography import BiographyCatalog
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog
from movie_breakdown.domain.global_analysis import (
    EntityCatalog,
    EventCatalog,
    RelationshipCatalog,
    StructureAnalysis,
)
from movie_breakdown.domain.manual_correction import CorrectionReceipt
from movie_breakdown.domain.run import ValidationReport
from movie_breakdown.domain.scene_analysis import SceneAnalysis
from movie_breakdown.domain.source import Screenplay


class NarrativeBreakdown(StrictModel):
    """叙事结构拆解所有已验证产物的稳定聚合。"""

    schema_version: str = "1.3"
    screenplay: Screenplay
    scene_analyses: list[SceneAnalysis]
    entities: EntityCatalog
    events: EventCatalog
    relationships: RelationshipCatalog
    dossiers: CharacterDossierCatalog
    biographies: BiographyCatalog
    structure: StructureAnalysis
    validation: ValidationReport
    correction_receipt: CorrectionReceipt | None = None
