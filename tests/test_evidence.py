import pytest

from movie_breakdown.application.evidence import EvidenceNormalizer, UnlocatableEvidenceError
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.scene_analysis import Evidence, SceneAnalysis
from movie_breakdown.domain.source import Scene, SourceSpan


def test_normalizer_replaces_paraphrase_with_exact_source_lines() -> None:
    scene = Scene(
        id="scene-0001",
        ordinal=1,
        heading="车站 日 外",
        text="车站 日 外\n小王推开车站大门。\n老李站在月台等候。",
        source_span=SourceSpan(line_start=10, line_end=12),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="小王抵达车站。",
        character_names=["小王", "老李"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=11, line_end=12),
                excerpt="小王进站……老李等待。",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer([scene]).normalize(analysis)

    assert normalized.evidence[0].excerpt == "小王推开车站大门。\n老李站在月台等候。"
    assert analysis.evidence[0].excerpt == "小王进站……老李等待。"


def test_normalizer_repairs_out_of_range_heading_span_from_excerpt() -> None:
    scene = Scene(
        id="scene-0132",
        ordinal=132,
        heading="海湾杂货铺 夜-内-外",
        text="131、海湾杂货铺 夜-内-外\n阿青等人冒雨进入店内。",
        source_span=SourceSpan(line_start=2080, line_end=2081),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="阿青等人进入杂货铺。",
        character_names=["阿青"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=2077, line_end=2077),
                excerpt="海湾杂货铺 夜-内-外",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer([scene]).normalize(analysis)

    assert normalized.evidence[0].source_span.line_start == 2080
    assert normalized.evidence[0].source_span.line_end == 2080
    assert normalized.evidence[0].excerpt == "131、海湾杂货铺 夜-内-外"


def test_strict_normalizer_removes_displayed_line_number_prefix() -> None:
    scene = Scene(
        id="scene-0001",
        ordinal=1,
        heading="社区礼堂",
        text="场景：社区礼堂\n△志愿者们准备演出。",
        source_span=SourceSpan(line_start=33, line_end=34),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="志愿者们准备演出。",
        character_names=[],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=99, line_end=99),
                excerpt="34: △志愿者们准备演出。",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer(
        [scene],
        require_excerpt_match=True,
    ).normalize(analysis)

    assert normalized.evidence[0].source_span.line_start == 34
    assert normalized.evidence[0].excerpt == "△志愿者们准备演出。"


def test_strict_normalizer_accepts_ordered_clauses_within_declared_span() -> None:
    scene = Scene(
        id="scene-0008",
        ordinal=8,
        heading="项目会议室",
        text=("△林舟把样卡给组员看。\n林舟：这是远航队蓝卡。\n△这时，组长把三张样卡放在桌上。"),
        source_span=SourceSpan(line_start=110, line_end=112),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="组员查看样卡。",
        character_names=["林舟"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=110, line_end=112),
                excerpt="林舟把样卡给组员看。\n组长把三张样卡放在桌上。",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer(
        [scene],
        require_excerpt_match=True,
    ).normalize(analysis)

    assert normalized.evidence[0].excerpt == scene.text


def test_strict_normalizer_accepts_declared_line_with_trailing_ellipsis() -> None:
    scene = Scene(
        id="scene-0010",
        ordinal=10,
        heading="灯塔控制室",
        text="总调度（os）：东侧信号已经中断，风暴正向这里靠近，我们必须守住北岭灯塔。",
        source_span=SourceSpan(line_start=170, line_end=170),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="总调度要求守住北岭灯塔。",
        character_names=["总调度"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=170, line_end=170),
                excerpt="总调度（os）：东侧信号已经中断……",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer(
        [scene],
        require_excerpt_match=True,
    ).normalize(analysis)

    assert normalized.evidence[0].excerpt == scene.text


def test_strict_normalizer_accepts_ordered_anchors_around_internal_ellipsis() -> None:
    scene = Scene(
        id="scene-0012",
        ordinal=12,
        heading="航海教室",
        text="△值班员熟练地在航线图下方写下：NO！This route is closed。",
        source_span=SourceSpan(line_start=199, line_end=199),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="值班员标记关闭航线。",
        character_names=["值班员"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=199, line_end=199),
                excerpt="在…下方写下：NO！This route is closed。",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer(
        [scene],
        require_excerpt_match=True,
    ).normalize(analysis)

    assert normalized.evidence[0].excerpt == scene.text


def test_strict_normalizer_shrinks_long_span_to_exact_anchor() -> None:
    middle = "△搜救队员沿山路继续前进。" * 30
    scene = Scene(
        id="scene-0009",
        ordinal=9,
        heading="山顶观测站",
        text=f"警报响起。\n{middle}\n两名队员把损坏的信标带回营地。",
        source_span=SourceSpan(line_start=147, line_end=149),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="搜救队员回收损坏的信标。",
        character_names=[],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=147, line_end=149),
                excerpt="警报响起。\n...\n两名队员把损坏的信标带回营地。",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer(
        [scene],
        require_excerpt_match=True,
    ).normalize(analysis)

    assert normalized.evidence[0].source_span == SourceSpan(line_start=149, line_end=149)
    assert normalized.evidence[0].excerpt == "两名队员把损坏的信标带回营地。"


def test_strict_normalizer_keeps_majority_exact_anchor_from_mixed_excerpt() -> None:
    scene = Scene(
        id="scene-0050",
        ordinal=50,
        heading="展馆入口（三岔走廊）",
        text=(
            "顾言：我们的口令记错了，演练成绩倒数第一。来，看张通行证。\n"
            "△顾言把通行证递给沈岚，借着走廊灯光，沈岚一看，是蓝鹭牌，立即警觉。"
        ),
        source_span=SourceSpan(line_start=1476, line_end=1477),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="顾言递出可疑通行证。",
        character_names=["顾言", "沈岚"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=scene.source_span,
                excerpt=(
                    "顾言：来，看张通行证。\n"
                    "△顾言把通行证递给沈岚，借着走廊灯光，沈岚一看，是蓝鹭牌"
                ),
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer(
        [scene],
        require_excerpt_match=True,
    ).normalize(analysis)

    assert normalized.evidence[0].source_span == SourceSpan(line_start=1477, line_end=1477)
    assert normalized.evidence[0].excerpt == scene.text.splitlines()[1]


def test_strict_normalizer_rejects_short_exact_fragment_beside_hallucination() -> None:
    scene = Scene(
        id="scene-0050",
        ordinal=50,
        heading="展馆入口",
        text="△顾言递通行证给沈岚。",
        source_span=SourceSpan(line_start=1477, line_end=1477),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="递烟。",
        character_names=[],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=scene.source_span,
                excerpt="完全不存在的长段落。\n顾言递通行证",
                confidence=Confidence.HIGH,
            )
        ],
    )

    with pytest.raises(UnlocatableEvidenceError):
        EvidenceNormalizer([scene], require_excerpt_match=True).normalize(analysis)


def test_strict_normalizer_uses_collective_majority_of_ordered_anchors() -> None:
    scene = Scene(
        id="scene-0050",
        ordinal=50,
        heading="展馆走廊",
        text=(
            "△顾言抓住资料袋，被沈岚伸手按住。\n"
            "△双方继续争抢。\n"
            "△顾言与沈岚拉扯，文件散落在走廊地面。\n"
            "△沈岚弯腰收拢文件。\n"
            "△顾言抽出备用门卡，在黑暗中打开侧门。"
        ),
        source_span=SourceSpan(line_start=1494, line_end=1498),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="二人在走廊争抢资料。",
        character_names=["顾言", "沈岚"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=scene.source_span,
                excerpt=(
                    "△顾言抓住资料袋，被沈岚伸手按住。\n...\n"
                    "△顾言与沈岚拉扯，文件散落在走廊地面。\n...\n"
                    "△沈岚弯腰收拢文件。\n"
                    "△顾言抽出备用门卡，在黑暗中打开侧门。\n...\n"
                    "△沈岚后来做了不存在的动作"
                ),
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalized = EvidenceNormalizer(
        [scene],
        require_excerpt_match=True,
    ).normalize(analysis)

    assert normalized.evidence[0].source_span == SourceSpan(line_start=1496, line_end=1496)
    assert normalized.evidence[0].excerpt == scene.text.splitlines()[2]


def test_normalizer_rejects_unlocatable_evidence_for_new_result() -> None:
    scene = Scene(
        id="scene-0001",
        ordinal=1,
        heading="车站 日 外",
        text="车站 日 外\n小王进入车站。",
        source_span=SourceSpan(line_start=10, line_end=11),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="小王进入车站。",
        character_names=["小王"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=100, line_end=101),
                excerpt="并不存在于剧本中的另一稿文字。",
                confidence=Confidence.HIGH,
            )
        ],
    )

    with pytest.raises(UnlocatableEvidenceError, match="证据无法在场景 scene-0001 中定位"):
        EvidenceNormalizer([scene]).normalize(analysis)


def test_normalizer_drops_unlocatable_evidence_during_cache_migration() -> None:
    scene = Scene(
        id="scene-0001",
        ordinal=1,
        heading="车站 日 外",
        text="车站 日 外\n小王进入车站。",
        source_span=SourceSpan(line_start=10, line_end=11),
        content_fingerprint="fingerprint",
    )
    analysis = SceneAnalysis(
        scene_id=scene.id,
        summary="小王进入车站。",
        character_names=["小王"],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=100, line_end=101),
                excerpt="并不存在于剧本中的另一稿文字。",
                confidence=Confidence.HIGH,
            )
        ],
    )

    normalizer = EvidenceNormalizer([scene], drop_unlocatable=True)

    normalized = normalizer.normalize(analysis)

    assert normalized.evidence == []
    assert normalizer.dropped_evidence == analysis.evidence


def test_normalizer_resets_dropped_evidence_for_each_normalization() -> None:
    scene = Scene(
        id="scene-0001",
        ordinal=1,
        heading="车站 日 外",
        text="车站 日 外\n小王进入车站。",
        source_span=SourceSpan(line_start=10, line_end=11),
        content_fingerprint="fingerprint",
    )
    invalid = SceneAnalysis(
        scene_id=scene.id,
        summary="坏证据。",
        character_names=[],
        objectives=[],
        obstacles=[],
        core_conflict=None,
        events=[],
        state_before=[],
        state_after=[],
        revelations=[],
        suspense=[],
        foreshadowing_candidates=[],
        plot_functions=[],
        uncertainties=[],
        evidence=[
            Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=100, line_end=100),
                excerpt="不存在的文字。",
                confidence=Confidence.LOW,
            )
        ],
    )
    valid = invalid.model_copy(
        update={
            "evidence": [
                Evidence(
                    scene_id=scene.id,
                    source_span=SourceSpan(line_start=11, line_end=11),
                    excerpt="小王进入车站。",
                    confidence=Confidence.HIGH,
                )
            ]
        }
    )
    normalizer = EvidenceNormalizer([scene], drop_unlocatable=True)

    normalizer.normalize(invalid)
    normalized = normalizer.normalize(valid)

    assert normalized.evidence[0].excerpt == "小王进入车站。"
    assert normalizer.dropped_evidence == []
