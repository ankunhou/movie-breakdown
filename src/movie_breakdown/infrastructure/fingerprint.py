"""稳定内容指纹和缓存键计算。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def hash_bytes(value: bytes) -> str:
    """计算字节内容的 SHA-256 十六进制指纹。

    Args:
        value: 需要计算指纹的原始字节。

    Returns:
        六十四位小写十六进制 SHA-256 指纹。
    """
    return hashlib.sha256(value).hexdigest()


def hash_text(value: str) -> str:
    """按 UTF-8 编码计算文本的 SHA-256 指纹。

    Args:
        value: 需要计算指纹的文本。

    Returns:
        文本 UTF-8 字节的 SHA-256 指纹。
    """
    return hash_bytes(value.encode("utf-8"))


def canonical_json(value: Any) -> str:
    """把模型或普通数据转换为键顺序稳定的紧凑 JSON。

    Args:
        value: Pydantic 模型或可 JSON 序列化的数据。

    Returns:
        不转义中文且键顺序稳定的紧凑 JSON。
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_computed_fields=True)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def content_fingerprint(value: Any) -> str:
    """计算结构化内容的稳定指纹。

    Args:
        value: Pydantic 模型或可 JSON 序列化的数据。

    Returns:
        结构化内容规范表示的 SHA-256 指纹。
    """
    return hash_text(canonical_json(value))


def schema_fingerprint(model_type: type[BaseModel]) -> str:
    """计算 Pydantic JSON Schema 的稳定指纹。

    Args:
        model_type: 需要追踪 Schema 变化的 Pydantic 模型类型。

    Returns:
        模型 JSON Schema 的 SHA-256 指纹。
    """
    return content_fingerprint(model_type.model_json_schema())


def cache_fingerprint(*components: Any) -> str:
    """把所有会影响结果的组件合并为缓存键。

    Args:
        components: 按稳定顺序提供的缓存影响因素。

    Returns:
        所有组件规范表示的 SHA-256 指纹。
    """
    return content_fingerprint(list(components))


def _json_default(value: Any) -> Any:
    """递归序列化容器内部出现的 Pydantic 模型。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_computed_fields=True)
    raise TypeError(f"不支持计算内容指纹的类型：{type(value).__name__}")
