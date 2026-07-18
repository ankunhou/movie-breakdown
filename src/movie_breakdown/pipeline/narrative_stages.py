"""可并发恢复的逐场分析与全局叙事分析阶段。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from movie_breakdown.application.evidence import EvidenceNormalizer
from movie_breakdown.application.ports import NarrativeAnalyzer
from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.run import Artifact, ProjectDocument
from movie_breakdown.domain.scene_analysis import SceneAnalysis, SceneAnalysisRecord
from movie_breakdown.domain.source import Scene, Screenplay
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.global_stages import (
    GlobalNarrativeStageService,
    GlobalStageResult,
)
from movie_breakdown.pipeline.model_support import model_failure_metadata, sum_usage
from movie_breakdown.pipeline.narrative_support import (
    merge_scene_retry_history,
    record_is_valid,
    validate_scene_analysis,
)
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime


@dataclass(frozen=True, slots=True)
class SceneStageResult:
    """逐场分析阶段返回给下游的稳定结果。

    Attributes:
        records: 按剧本顺序排列的逐场分析记录。
        artifact_fingerprint: 全部记录的聚合内容指纹。
    """

    records: list[SceneAnalysisRecord]
    artifact_fingerprint: str


class NarrativeStageService:
    """执行使用叙事分析模型的流水线阶段。

    Attributes:
        runtime: 当前项目的阶段运行时。
        analyzer: 符合应用端口的叙事分析策略。
    """

    def __init__(self, runtime: StageRuntime, analyzer: NarrativeAnalyzer) -> None:
        """创建模型分析阶段服务。

        Args:
            runtime: 当前流水线运行时。
            analyzer: 逐场与全局叙事分析实现。
        """
        self.runtime = runtime
        self.analyzer = analyzer

    def analyze_scenes(
        self,
        project: ProjectDocument,
        screenplay: Artifact[Screenplay],
    ) -> SceneStageResult:
        """并发分析未命中缓存的场景并逐个保存结果。

        Args:
            project: 当前项目描述及模型配置。
            screenplay: 已验证的场景切分产物。

        Returns:
            按剧本顺序排列的记录和聚合指纹。

        Raises:
            PipelineStageError: 至少一个场景最终分析失败。
        """
        scenes = screenplay.data.scenes
        expected = {scene.id: self._scene_cache_key(scene, project) for scene in scenes}
        overall_key = cache_fingerprint(
            screenplay.metadata.artifact_fingerprint,
            get_stage("scene_analysis").version,
            [expected[scene.id] for scene in scenes],
        )
        try:
            existing = self.runtime.store.read_jsonl("scene_analysis", SceneAnalysisRecord)
        except (OSError, ValueError):
            existing = []
        records = {record.scene_id: record for record in existing if record.scene_id in expected}
        pending = [
            scene
            for scene in scenes
            if not record_is_valid(records.get(scene.id), expected[scene.id])
        ]
        if not pending and len(records) == len(scenes):
            ordered = [records[scene.id] for scene in scenes]
            fingerprint = content_fingerprint(ordered)
            self.runtime.cached("scene_analysis", overall_key, fingerprint)
            return SceneStageResult(ordered, fingerprint)

        self.runtime.start(
            "scene_analysis",
            overall_key,
            f"分析 {len(pending)} 个未缓存场景",
        )
        completed = 0
        with ThreadPoolExecutor(max_workers=project.config.concurrency) as executor:
            futures = {
                executor.submit(self._analyze_one, scene, project): scene for scene in pending
            }
            for future in as_completed(futures):
                scene = futures[future]
                current = future.result()
                records[scene.id] = merge_scene_retry_history(
                    current,
                    records.get(scene.id),
                )
                completed += 1
                ordered_partial = [records[item.id] for item in scenes if item.id in records]
                self.runtime.store.write_jsonl("scene_analysis", ordered_partial)
                self.runtime.notify(
                    "scene_analysis",
                    f"已完成 {completed}/{len(pending)}：{scene.id}",
                )

        ordered = [records[scene.id] for scene in scenes]
        failed = [record for record in ordered if record.status != StageStatus.SUCCESS]
        fingerprint = content_fingerprint(ordered)
        usage = sum_usage(record.usage for record in ordered)
        if failed:
            message = f"{len(failed)} 个场景分析失败，可使用 resume 重试。"
            self.runtime.fail("scene_analysis", message, usage)
            raise PipelineStageError(message)
        self.runtime.success("scene_analysis", overall_key, fingerprint, usage)
        return SceneStageResult(ordered, fingerprint)

    def analyze_global(
        self,
        project: ProjectDocument,
        screenplay: Artifact[Screenplay],
        scene_result: SceneStageResult,
    ) -> GlobalStageResult:
        """汇总逐场结果并生成全局实体、关系与三幕结构。

        Args:
            project: 当前项目描述及模型配置。
            screenplay: 已验证的场景切分产物。
            scene_result: 全部成功的逐场分析结果。

        Returns:
            全局分析聚合和内容指纹。

        Raises:
            PipelineStageError: 全局结构化模型调用失败。
        """
        return GlobalNarrativeStageService(self.runtime, self.analyzer).analyze(
            project,
            screenplay,
            scene_result,
        )

    def _scene_cache_key(self, scene: Scene, project: ProjectDocument) -> str:
        """计算单场内容、Prompt、Schema 和模型配置的缓存键。"""
        config = project.config
        return cache_fingerprint(
            scene.content_fingerprint,
            get_stage("scene_analysis").version,
            self.analyzer.scene_prompt_fingerprint,
            schema_fingerprint(SceneAnalysis),
            config.model,
            config.thinking_enabled,
            config.reasoning_effort,
        )

    def _analyze_one(self, scene: Scene, project: ProjectDocument) -> SceneAnalysisRecord:
        """把单场模型成功或失败统一转换为可恢复记录。"""
        cache_key = self._scene_cache_key(scene, project)
        call = None
        try:
            call = self.analyzer.analyze_scene(scene, project.config)
            analysis = EvidenceNormalizer([scene]).normalize(call.content)
            validate_scene_analysis(scene, analysis)
            return SceneAnalysisRecord(
                scene_id=scene.id,
                cache_key=cache_key,
                status=StageStatus.SUCCESS,
                analysis=analysis,
                attempts=call.attempts,
                usage=call.usage,
            )
        except Exception as error:
            attempts, usage = model_failure_metadata(error, call, project.config.max_retries + 1)
            return SceneAnalysisRecord(
                scene_id=scene.id,
                cache_key=cache_key,
                status=StageStatus.FAILED,
                error=str(error)[:4000],
                attempts=attempts,
                usage=usage,
            )
