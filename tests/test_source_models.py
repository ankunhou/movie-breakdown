import pytest
from pydantic import ValidationError

from movie_breakdown.domain.source import SourceSpan


def test_domain_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SourceSpan(line_start=1, line_end=2, unexpected=True)


def test_source_span_rejects_zero_based_lines() -> None:
    with pytest.raises(ValidationError):
        SourceSpan(line_start=0, line_end=2)


def test_source_span_rejects_reversed_range() -> None:
    with pytest.raises(ValidationError, match="结束行号"):
        SourceSpan(line_start=3, line_end=2)
