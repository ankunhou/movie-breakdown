"""CLI 选项枚举与稳定退出码。"""

from enum import IntEnum, StrEnum


class ExitCode(IntEnum):
    """CLI 稳定退出码。"""

    SUCCESS = 0
    ERROR = 1
    VALIDATION_FAILED = 2


class StructureFramework(StrEnum):
    """CLI 支持的叙事结构分析框架。"""

    THREE_ACT = "three-act"


class FormatDetection(StrEnum):
    """场景格式识别策略。"""

    AUTO = "auto"
    LOCAL = "local"
    MODEL = "model"


class ExportChoice(StrEnum):
    """CLI 支持的正式导出格式。"""

    MARKDOWN = "markdown"
    JSON = "json"
    ALL = "all"


class ProductionExportChoice(StrEnum):
    """CLI 支持的独立制片导出格式。"""

    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"
    ALL = "all"
