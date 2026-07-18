"""全局叙事分析证据恢复的可审计领域模型。"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.scene_analysis import Evidence


class GlobalEvidenceRecoveryReport(StrictModel):
    """记录全局分析是否经过坏证据删除后才通过校验。

    报告同时支持严格结果直接通过与可审计恢复两种状态。发生恢复时，
    必须保留首次严格校验错误和全部被删除证据，避免宽松处理静默发生。

    Attributes:
        source_fingerprint: 本次分析所绑定的规范化剧本指纹。
        cache_key: 本次全局分析输入与模型配置共同生成的缓存键。
        recovered: 是否通过删除无法定位的证据完成恢复。
        initial_error: 首次严格证据校验的错误信息。
        dropped_evidence: 恢复过程中删除的原始证据。
        result_fingerprint: 最终全局分析结果的内容指纹。
    """

    schema_version: str = "1.0"
    source_fingerprint: str = Field(min_length=1, max_length=128)
    cache_key: str = Field(min_length=1, max_length=128)
    recovered: bool
    initial_error: str | None = Field(default=None, min_length=1, max_length=2000)
    dropped_evidence: list[Evidence] = Field(default_factory=list)
    result_fingerprint: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_recovery_state(self) -> Self:
        """保证恢复标记与审计明细相互一致。

        Returns:
            状态一致的证据恢复报告。

        Raises:
            ValueError: 恢复状态缺少错误或被删除证据，或正常状态携带恢复明细。
        """
        has_error = self.initial_error is not None and bool(self.initial_error.strip())
        if self.recovered and (not has_error or not self.dropped_evidence):
            raise ValueError("证据恢复报告必须记录首次错误和至少一条被删除证据。")
        if not self.recovered and (self.initial_error is not None or self.dropped_evidence):
            raise ValueError("未发生证据恢复时不得携带恢复错误或被删除证据。")
        return self
