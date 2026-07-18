from pathlib import Path


def test_python_source_files_do_not_exceed_300_lines() -> None:
    source_root = Path(__file__).parents[1] / "src"
    oversized = {
        str(path.relative_to(source_root)): len(path.read_text("utf-8").splitlines())
        for path in source_root.rglob("*.py")
        if len(path.read_text("utf-8").splitlines()) > 300
    }

    assert oversized == {}
