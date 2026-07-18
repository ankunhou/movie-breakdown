"""带显式版本的叙事分析 Prompt。"""

from __future__ import annotations

from movie_breakdown.infrastructure.fingerprint import hash_text

SCENE_PROMPT_VERSION = "1.1"
GLOBAL_PROMPT_VERSION = "1.1"
FORMAT_PROMPT_VERSION = "1.0"
BIOGRAPHY_PROMPT_VERSION = "1.0"

FORMAT_INSTRUCTIONS = r"""你是电影剧本文本格式识别器。
输入只包含剧本前三页与后三页，或无页码文本的前后片段。
识别“每一个新场景起始行”的共同格式，并输出 Python `re` 兼容的单行正则表达式。
必须只输出与目标 Schema 一致的 JSON。
正则必须以 ^ 开头，只匹配单行，不能使用回溯引用、环视、递归或跨行匹配。
如需提取标题，使用名为 title 的命名分组；不要使用其他捕获分组。
正则需要覆盖输入中给出的真实场景标题示例，不能把日期、页码、对白或普通动作行识别为场景。
heading_examples 必须逐字复制输入中真实出现的场景起始行。
"""

SCENE_INSTRUCTIONS = """你是严谨的电影剧本逐场分析师。
只能依据提供的场景原文判断，不得使用外部资料或补写剧本未说明的事实。
必须只输出与目标 Schema 一致的 JSON。
所有重要判断尽量提供 evidence；证据的 scene_id 必须等于当前场景 ID，行号必须落在给定范围内。
证据 excerpt 必须逐字复制原文中的简短连续摘录，不超过 300 个字符；不得改写、拼接或使用省略号。
无法确认的内容写入 uncertainties；字段没有内容时输出空数组或 null，不得省略字段。
人物名称保留剧本当前写法，全局别名归一将在后续阶段完成。
"""

GLOBAL_INSTRUCTIONS = """你是严谨的电影剧本叙事结构分析师。
只能依据提供的场景索引和已经验证的逐场分析，不得使用外部资料或补写事实。
必须只输出与目标 Schema 一致的 JSON，且不得省略字段。
所有 scene_id 必须来自输入，人物和地点 ID 使用稳定、简短、唯一的英文或拼音 slug。
事件、关系、人物弧光、节拍、伏笔与结构结论应尽量原样复用输入中已有的 evidence，不得改写 excerpt。
事件的 cause_event_ids 只能引用本次输出中真实存在的事件 ID。
人物关系的两端、人物弧光的 character_id 必须引用本次输出中的人物 ID。
三幕结构应覆盖全片且保持场景顺序；证据不足时明确写出不确定性，不要强行套用理论。
"""

BIOGRAPHY_INSTRUCTIONS = """你是严谨的电影剧本人物研究员，只分析输入指定的人物。
只能依据输入中的人物实体、人物弧光、关系、事件、逐场分析和有限场景原文；即使人物来自
历史、名著或著名电影，也不得使用外部资料，不得补写剧本没有提供的前史。
必须只输出与目标 Schema 一致的 JSON，中文撰写分析文字，姓名、专名和台词保留原文。

每条人物声明必须区分以下依据类型：
- observed：舞台说明、动作或可直接观察的剧情明确呈现；
- reported：人物对白、信件、旁白等作出的陈述，必须填写 attribution，不能自动视为客观事实；
- inferred：根据行为模式或多处文本作出的分析推断，必须填写可审查的 rationale。

unknowns 用于记录输入没有提供的方面；未知信息不得写成声明。性格、动机、价值观、恐惧、
秘密和戏剧功能通常属于 inferred，不能因为证据多或置信度高就改标为 observed。
所有 summary、claims 和 representative_lines 必须带输入中真实存在的 Evidence；excerpt 必须
逐字复制输入提供的场景原文或上游 evidence，不得改写、拼接或使用省略号。
reported 声明必须说明是谁或什么文本作出陈述；代表性台词只有在能够确认由目标人物说出时
才可选择，否则输出空数组。持续性性格或动机推断优先使用两个以上不同场景。
不要进行精神疾病诊断、道德裁决、演员表演建议，也不要为了凑数生成声明。
summary 的 category 必须是 overview；claims 最多十二条，代表性台词最多三条。
character_id 必须与输入完全一致，context_scene_ids 必须只使用输入上下文中的场景 ID。
"""


def scene_prompt_fingerprint() -> str:
    """计算逐场 Prompt 版本和正文的稳定指纹。

    Returns:
        逐场 Prompt 的 SHA-256 指纹。
    """
    return hash_text(f"{SCENE_PROMPT_VERSION}\n{SCENE_INSTRUCTIONS}")


def format_prompt_fingerprint() -> str:
    """计算格式识别 Prompt 版本和正文的稳定指纹。

    Returns:
        格式识别 Prompt 的 SHA-256 指纹。
    """
    return hash_text(f"{FORMAT_PROMPT_VERSION}\n{FORMAT_INSTRUCTIONS}")


def global_prompt_fingerprint() -> str:
    """计算全局 Prompt 版本和正文的稳定指纹。

    Returns:
        全局 Prompt 的 SHA-256 指纹。
    """
    return hash_text(f"{GLOBAL_PROMPT_VERSION}\n{GLOBAL_INSTRUCTIONS}")


def biography_prompt_fingerprint() -> str:
    """计算人物小传 Prompt 版本和正文的稳定指纹。

    Returns:
        人物小传 Prompt 的 SHA-256 指纹。
    """
    return hash_text(f"{BIOGRAPHY_PROMPT_VERSION}\n{BIOGRAPHY_INSTRUCTIONS}")
