from importlib.metadata import version

from movie_breakdown import __version__


def test_public_version_comes_from_installed_package_metadata() -> None:
    assert __version__ == version("movie-breakdown")
