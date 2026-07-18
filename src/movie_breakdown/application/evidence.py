"""根据模型选择的来源范围回填可逐字核验的剧本证据。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene


class UnlocatableEvidenceError(ValueError):
    """表示一条结构合法的证据无法在其声明场景中定位。"""


class EvidenceNormalizer:
    """把任意结构化分析中的 Evidence 摘录替换为对应原文。

    Attributes:
        scenes: 场景 ID 到原始场景的只读索引。
        drop_unlocatable: 是否在旧缓存迁移时删除无法定位的证据。
        require_excerpt_match: 是否要求输入摘录先在声明场景中逐字出现。
        dropped_evidence: 最近一次规范化时因无法定位而删除的原始证据。
    """

    def __init__(
        self,
        scenes: Iterable[Scene],
        *,
        drop_unlocatable: bool = False,
        require_excerpt_match: bool = False,
    ) -> None:
        """创建基于当前剧本场景集合的证据规范化器。

        Args:
            scenes: 可被证据引用的原始场景。
            drop_unlocatable: 是否允许旧缓存迁移删除无法定位的证据；新模型结果应保持否。
            require_excerpt_match: 是否拒绝只凭合法行号改写、但原摘录不在场景中的证据。
        """
        self.scenes = {scene.id: scene for scene in scenes}
        self.drop_unlocatable = drop_unlocatable
        self.require_excerpt_match = require_excerpt_match
        self.dropped_evidence: list[Evidence] = []

    def normalize[T: BaseModel](self, model: T) -> T:
        """按来源行号回填模型中的全部证据摘录。

        Args:
            model: 包含任意层级 Evidence 的 Pydantic 模型。

        Returns:
            与输入相同类型、证据摘录可在原文逐字找到的新模型。

        Raises:
            UnlocatableEvidenceError: 严格模式下证据无法在引用场景中定位。
        """
        self.dropped_evidence = []
        payload = model.model_dump(mode="python")
        self._walk(payload)
        return type(model).model_validate(payload)

    def _walk(self, value: Any) -> bool:
        """递归规范化证据，并从列表中移除无法定位的证据。

        Args:
            value: 当前递归访问的 JSON 兼容值。

        Returns:
            当前值是否可保留；只有无法在原场景定位的 Evidence 返回否。
        """
        if isinstance(value, list):
            value[:] = [item for item in value if self._walk(item)]
            return True
        if not isinstance(value, dict):
            return True
        if {"scene_id", "source_span", "excerpt"} <= value.keys():
            self._repair_span_from_excerpt(value)
            excerpt = self._excerpt(value["scene_id"], value["source_span"])
            if excerpt is None:
                if self.drop_unlocatable:
                    self.dropped_evidence.append(Evidence.model_validate(value))
                    return False
                raise UnlocatableEvidenceError(f"证据无法在场景 {value['scene_id']} 中定位。")
            value["excerpt"] = excerpt
        for item in value.values():
            self._walk(item)
        return True

    def _repair_span_from_excerpt(self, evidence: dict[str, Any]) -> None:
        """在模型行号越界时用逐字摘录反查场景内的真实范围。

        Args:
            evidence: 待检查且可能被原地修复的证据字典。

        Raises:
            UnlocatableEvidenceError: 严格模式下输入摘录未在声明场景中出现。
        """
        scene = self.scenes.get(evidence["scene_id"])
        if scene is None:
            return
        declared_excerpt = self._excerpt(
            scene.id,
            evidence["source_span"],
            limit=None,
        )
        excerpt = str(evidence["excerpt"]).strip()
        if declared_excerpt == excerpt:
            return
        if declared_excerpt is not None and not self.require_excerpt_match:
            return
        located = next(
            (
                (candidate, scene.text.find(candidate))
                for candidate in self._excerpt_candidates(excerpt)
                if candidate and scene.text.find(candidate) >= 0
            ),
            None,
        )
        if located is None:
            if (
                declared_excerpt is not None
                and self.require_excerpt_match
                and self._declared_span_supports_excerpt(declared_excerpt, excerpt)
            ):
                if len(declared_excerpt) > 300 and not self._shrink_span_to_exact_anchor(
                    evidence,
                    declared_excerpt,
                    excerpt,
                ):
                    raise UnlocatableEvidenceError("超长证据范围中没有可逐字保留的有效锚点。")
                return
            if (
                declared_excerpt is not None
                and self.require_excerpt_match
                and self._shrink_span_to_exact_anchor(
                    evidence,
                    declared_excerpt,
                    excerpt,
                    require_majority=True,
                )
            ):
                return
            if declared_excerpt is not None and self.require_excerpt_match:
                raise UnlocatableEvidenceError(
                    f"证据摘录无法在场景 {scene.id} 中逐字定位："
                    f"{excerpt[:100]!r}，声明行号 {evidence['source_span']}。"
                )
            return
        excerpt, offset = located
        line_start = scene.source_span.line_start + scene.text[:offset].count("\n")
        line_end = line_start + excerpt.count("\n")
        if line_end > scene.source_span.line_end:
            return
        span = evidence["source_span"]
        evidence["source_span"] = {
            **span,
            "line_start": line_start,
            "line_end": line_end,
        }

    @staticmethod
    def _shrink_span_to_exact_anchor(
        evidence: dict[str, Any],
        declared_excerpt: str,
        excerpt: str,
        *,
        require_majority: bool = False,
    ) -> bool:
        """把超长声明范围收紧到其中最长的逐字锚点。

        Args:
            evidence: 待原地更新行号的证据字典。
            declared_excerpt: 声明行号覆盖的完整连续原文。
            excerpt: 模型用省略号或换行连接的证据摘录。
            require_majority: 是否要求锚点覆盖摘录非空白字符的一半以上。

        Returns:
            找到足够长的精确锚点并完成收紧时返回真，否则返回假。
        """
        minimum_length = 8 if require_majority else 4
        candidates = [
            part.strip()
            for part in re.split(r"(?:…+|\.{3,}|\n+)", excerpt)
            if minimum_length <= len(part.strip()) <= 300
        ]
        located: list[tuple[str, int]] = []
        search_offset = 0
        for candidate in candidates:
            position = declared_excerpt.find(candidate, search_offset)
            if position >= 0:
                located.append((candidate, position))
                search_offset = position + len(candidate)
        if not located:
            return False
        anchor, offset = max(located, key=lambda item: len(item[0]))
        if require_majority:
            supported_length = sum(
                len(re.sub(r"[\s△.…]", "", candidate)) for candidate, _ in located
            )
            excerpt_length = len(re.sub(r"[\s△.…]", "", excerpt))
            if supported_length * 2 < excerpt_length:
                return False
        span = evidence["source_span"]
        line_start = int(span["line_start"]) + declared_excerpt[:offset].count("\n")
        evidence["source_span"] = {
            **span,
            "line_start": line_start,
            "line_end": line_start + anchor.count("\n"),
        }
        return True

    @staticmethod
    def _excerpt_candidates(excerpt: str) -> list[str]:
        """生成仍能逐字回到原文的编号和引号清理候选。"""
        without_numbers = "\n".join(
            re.sub(r"^\s*\d+\s*[:：]\s?", "", line) for line in excerpt.splitlines()
        ).strip()
        candidates = [excerpt, without_numbers]
        for value in tuple(candidates):
            if len(value) >= 2 and value[0] + value[-1] in {'""', "''", "“”", "‘’"}:
                candidates.append(value[1:-1].strip())
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _declared_span_supports_excerpt(declared: str, excerpt: str) -> bool:
        """判断模型摘录的有序句段是否均逐字存在于声明行号范围。"""
        declared_compact = re.sub(r"[\s△]", "", declared)
        clauses: list[str] = []
        for value in re.split(r"[。！？!?\n]+", excerpt):
            compact = re.sub(r"[\s△]", "", value)
            clauses.extend(anchor for anchor in re.split(r"(?:…+|\.{3,})", compact) if anchor)
        if sum(map(len, clauses)) < 4:
            return False
        offset = 0
        for clause in clauses:
            position = declared_compact.find(clause, offset)
            if position < 0:
                return False
            offset = position + len(clause)
        return True

    def _excerpt(
        self,
        scene_id: str,
        span: dict[str, Any],
        *,
        limit: int | None = 300,
    ) -> str | None:
        """从场景与全局行号范围提取连续原文。

        Args:
            scene_id: 证据声明的场景 ID。
            span: 证据声明的全局行号范围。
            limit: 最大返回字符数；空值表示读取完整声明范围。

        Returns:
            可定位时返回连续原文，否则返回空值。
        """
        scene = self.scenes.get(scene_id)
        if scene is None:
            return None
        start = int(span["line_start"]) - scene.source_span.line_start
        end = int(span["line_end"]) - scene.source_span.line_start + 1
        lines = scene.text.splitlines()
        if start < 0 or end > len(lines) or start >= end:
            return None
        excerpt = "\n".join(lines[start:end]).strip()
        return excerpt[:limit].rstrip() if limit is not None else excerpt
