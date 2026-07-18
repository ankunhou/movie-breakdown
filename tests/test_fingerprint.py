from pydantic import BaseModel

from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)


class _Example(BaseModel):
    name: str
    count: int


def test_content_fingerprint_ignores_dictionary_order() -> None:
    left = content_fingerprint({"name": "剧本", "count": 2})
    right = content_fingerprint({"count": 2, "name": "剧本"})

    assert left == right


def test_cache_fingerprint_changes_with_any_component() -> None:
    baseline = cache_fingerprint("source", "prompt-v1", "model-a")

    assert baseline != cache_fingerprint("source", "prompt-v2", "model-a")
    assert baseline != cache_fingerprint("source", "prompt-v1", "model-b")


def test_schema_fingerprint_is_stable() -> None:
    assert schema_fingerprint(_Example) == schema_fingerprint(_Example)


def test_content_fingerprint_supports_models_nested_in_list() -> None:
    left = content_fingerprint([_Example(name="剧本", count=2)])
    right = content_fingerprint([{"name": "剧本", "count": 2}])

    assert left == right
