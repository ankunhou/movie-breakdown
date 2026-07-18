"""独立、可并发恢复的制片逐场模型阶段。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from movie_breakdown.application.evidence import EvidenceNormalizer
from movie_breakdown.application.production_ports import ProductionAnalyzer
from movie_breakdown.application.production_recovery import (
    normalize_production_identity,
    normalize_production_references,
)
from movie_breakdown.application.production_validation import ProductionValidationService
from movie_breakdown.domain.base import Severity, StageStatus
from movie_breakdown.domain.production_run import ProductionProject
from movie_breakdown.domain.production_scene import (
    SceneProductionAnalysis,
    SceneProductionRecord,
)
from movie_breakdown.domain.run import Artifact
from movie_breakdown.domain.source import Scene, Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.pipeline.model_support import model_failure_metadata, sum_usage
from movie_breakdown.pipeline.production_scene_cache import (
    production_scene_cache_key,
    production_scene_stage_cache_key,
)
from movie_breakdown.pipeline.production_scene_support import (
    merge_production_retry_history,
    production_record_is_valid,
)
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime


@dataclass(frozen=True, slots=True)
class ProductionSceneStageResult:
    """制片逐场阶段返回给本地聚合的稳定结果。

    Attributes:
        records: 按剧本顺序排列的制片逐场记录。
        artifact_fingerprint: 全部记录的聚合内容指纹。
    """

    records: list[SceneProductionRecord]
    artifact_fingerprint: str


class ProductionSceneStageService:
    """执行只依赖共享场景原文的制片模型阶段。

    Attributes:
        runtime: 独立 production manifest 的阶段运行时。
        analyzer: 符合制片应用端口的模型策略。
    """

    def __init__(self, runtime: StageRuntime, analyzer: ProductionAnalyzer) -> None:
        """创建制片逐场阶段服务。

        Args:
            runtime: 绑定制片仓库和阶段注册表的运行时。
            analyzer: 单场制片分析实现。
        """
        self.runtime = runtime
        self.analyzer = analyzer

    def analyze(
        self,
        project: ProductionProject,
        screenplay: Artifact[Screenplay],
    ) -> ProductionSceneStageResult:
        """并发分析未命中缓存的场景并逐条持久化。

        Args:
            project: 独立制片项目和模型配置。
            screenplay: 主项目提供的共享只读场景产物。

        Returns:
            按场景顺序排列的记录和聚合指纹。

        Raises:
            PipelineStageError: 至少一个场景最终分析失败。
        """
        scenes = screenplay.data.scenes
        expected = {scene.id: self._scene_cache_key(scene, project) for scene in scenes}
        overall_key = production_scene_stage_cache_key(
            screenplay.metadata.artifact_fingerprint,
            scenes,
            expected,
        )
        try:
            existing = self.runtime.store.read_jsonl(
                "scene_elements",
                SceneProductionRecord,
            )
        except (OSError, ValueError) as error:
            message = (
                f"制片逐场缓存 scene_elements.jsonl 无法安全读取或校验，已中止且未调用模型：{error}"
            )
            self.runtime.fail("production_scene_analysis", message)
            raise PipelineStageError(message) from error
        records = {record.scene_id: record for record in existing if record.scene_id in expected}
        recovered = False
        for scene in scenes:
            current = records.get(scene.id)
            restored = self._normalize_cached_record(scene, current, expected[scene.id])
            if restored is not current:
                records[scene.id] = restored
                recovered = True
        if recovered:
            ordered_partial = [records[scene.id] for scene in scenes if scene.id in records]
            self.runtime.store.write_jsonl("scene_elements", ordered_partial)
        pending = [
            scene
            for scene in scenes
            if not production_record_is_valid(records.get(scene.id), expected[scene.id])
        ]
        if not pending and len(records) == len(scenes):
            ordered = [records[scene.id] for scene in scenes]
            if existing != ordered:
                self.runtime.store.write_jsonl("scene_elements", ordered)
            fingerprint = content_fingerprint(ordered)
            self.runtime.cached("production_scene_analysis", overall_key, fingerprint)
            return ProductionSceneStageResult(ordered, fingerprint)
        self.runtime.start(
            "production_scene_analysis",
            overall_key,
            f"拆解 {len(pending)} 个未缓存场景的制片元素",
        )
        self._run_pending(project, scenes, pending, records)
        ordered = [records[scene.id] for scene in scenes]
        failed = [record for record in ordered if record.status != StageStatus.SUCCESS]
        fingerprint = content_fingerprint(ordered)
        usage = sum_usage(record.usage for record in ordered)
        if failed:
            message = f"{len(failed)} 个场景制片拆解失败，可使用 production resume 重试。"
            self.runtime.fail("production_scene_analysis", message, usage)
            raise PipelineStageError(message)
        self.runtime.success("production_scene_analysis", overall_key, fingerprint, usage)
        return ProductionSceneStageResult(ordered, fingerprint)

    def _run_pending(
        self,
        project: ProductionProject,
        scenes: list[Scene],
        pending: list[Scene],
        records: dict[str, SceneProductionRecord],
    ) -> None:
        """并发执行待分析场景并在每场结束后保存恢复点。"""
        completed = 0
        with ThreadPoolExecutor(max_workers=project.config.concurrency) as executor:
            futures = {
                executor.submit(self._analyze_one, scene, project): scene for scene in pending
            }
            for future in as_completed(futures):
                scene = futures[future]
                records[scene.id] = merge_production_retry_history(
                    future.result(),
                    records.get(scene.id),
                )
                completed += 1
                ordered_partial = [records[item.id] for item in scenes if item.id in records]
                self.runtime.store.write_jsonl("scene_elements", ordered_partial)
                self.runtime.notify(
                    "production_scene_analysis",
                    f"已完成 {completed}/{len(pending)}：{scene.id}",
                )

    def _scene_cache_key(self, scene: Scene, project: ProductionProject) -> str:
        """计算只包含场景、制片契约和模型配置的缓存键。"""
        return production_scene_cache_key(
            scene,
            project,
            self.analyzer.production_prompt_fingerprint,
        )

    def _analyze_one(
        self,
        scene: Scene,
        project: ProductionProject,
    ) -> SceneProductionRecord:
        """把单场模型成功或失败转换为可恢复制片记录。"""
        cache_key = self._scene_cache_key(scene, project)
        call = None
        try:
            call = self.analyzer.analyze_scene(scene, project.config)
            analysis = self._normalize_analysis(scene, call.content)
            return SceneProductionRecord(
                scene_id=scene.id,
                cache_key=cache_key,
                status=StageStatus.SUCCESS,
                analysis=analysis,
                attempts=call.attempts,
                usage=call.usage,
            )
        except Exception as error:
            attempts, usage = model_failure_metadata(
                error,
                call,
                project.config.max_retries + 1,
            )
            return SceneProductionRecord(
                scene_id=scene.id,
                cache_key=cache_key,
                status=StageStatus.FAILED,
                analysis=call.content if call is not None else None,
                error=str(error)[:4000],
                attempts=attempts,
                usage=usage,
            )

    def _normalize_cached_record(
        self,
        scene: Scene,
        record: SceneProductionRecord | None,
        cache_key: str,
    ) -> SceneProductionRecord | None:
        """重新规范化同缓存键的成功记录，并迁移可恢复的失败记录。

        Args:
            scene: 缓存记录应对应的当前场景原文。
            record: 从 JSONL 读取的可选逐场记录。
            cache_key: 当前场景和模型契约对应的预期缓存键。

        Returns:
            可复用的规范化记录；无法恢复的成功记录会转为失败以触发重跑。
        """
        if (
            record is None
            or record.analysis is None
            or record.cache_key != cache_key
            or record.status not in {StageStatus.SUCCESS, StageStatus.FAILED}
        ):
            return record
        try:
            analysis = self._normalize_analysis(scene, record.analysis)
        except (ValueError, TypeError) as error:
            if record.status == StageStatus.SUCCESS:
                return record.model_copy(
                    update={
                        "status": StageStatus.FAILED,
                        "error": f"缓存确定性校验失败，需重新拆解：{error}"[:4000],
                    }
                )
            return record
        if record.status == StageStatus.SUCCESS and analysis == record.analysis:
            return record
        return record.model_copy(
            update={
                "status": StageStatus.SUCCESS,
                "analysis": analysis,
                "error": None,
            }
        )

    @staticmethod
    def _normalize_analysis(
        scene: Scene,
        analysis: SceneProductionAnalysis,
    ) -> SceneProductionAnalysis:
        """统一执行身份、证据、引用恢复和单场阻断校验。"""
        content = normalize_production_identity(scene, analysis)
        normalized = EvidenceNormalizer(
            [scene],
            require_excerpt_match=True,
        ).normalize(content)
        normalized = normalize_production_references(normalized)
        issues = ProductionValidationService().validate_analysis(scene, normalized)
        errors = [issue for issue in issues if issue.severity == Severity.ERROR]
        if errors:
            detail = "；".join(f"{issue.code}: {issue.message}" for issue in errors)
            raise ValueError(detail)
        return normalized
