"""组合全人物档案与核心人物小传的一致性校验。"""

from __future__ import annotations

from movie_breakdown.application.biography_validation import BiographyValidationService
from movie_breakdown.application.dossier_validation import CharacterDossierValidationService
from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.character_biography import BiographyCatalog
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog
from movie_breakdown.domain.global_analysis import GlobalAnalysisResult
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.source import Screenplay


class CharacterArtifactValidationService:
    """协调人物档案存在性、全量覆盖和核心小传深度校验。"""

    def validate(
        self,
        screenplay: Screenplay,
        global_result: GlobalAnalysisResult,
        biographies: BiographyCatalog | None,
        dossiers: CharacterDossierCatalog | None,
        issues: list[ValidationIssue],
        *,
        require_biographies: bool,
        require_dossiers: bool,
    ) -> None:
        """把人物相关产物问题追加到总校验报告。

        Args:
            screenplay: 提供场景原文和有效引用的完整剧本。
            global_result: 提供人物、事件、关系和弧光的全局结果。
            biographies: 可选的核心人物完整小传目录。
            dossiers: 可选的全人物分级档案目录。
            issues: 接收新增校验问题的可变列表。
            require_biographies: 是否把核心人物小传缺失视为错误。
            require_dossiers: 是否把全人物档案缺失视为错误。
        """
        if dossiers is None:
            if require_dossiers:
                issues.append(
                    _missing_issue(
                        "dossier.missing",
                        "缺少全人物分级档案，请先执行 resume。",
                    )
                )
        else:
            CharacterDossierValidationService().validate(
                dossiers,
                biographies,
                screenplay,
                global_result,
                issues,
            )
        if biographies is None:
            if require_biographies:
                issues.append(
                    _missing_issue(
                        "biography.missing",
                        "缺少人物小传产物，请先执行 resume。",
                    )
                )
        else:
            BiographyValidationService().validate(
                biographies,
                screenplay,
                global_result,
                issues,
                dossiers,
            )


def _missing_issue(code: str, message: str) -> ValidationIssue:
    """创建未绑定具体引用的缺失产物错误。"""
    return ValidationIssue(severity=Severity.ERROR, code=code, message=message)
