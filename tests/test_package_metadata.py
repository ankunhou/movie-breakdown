from importlib.metadata import metadata, version

from movie_breakdown import __version__


def test_public_version_comes_from_installed_package_metadata() -> None:
    assert __version__ == version("movie-breakdown")

    package_metadata = metadata("movie-breakdown")
    assert "Andy (ankunhou)" in package_metadata["Author-email"]
    assert "houankun@hotmail.com" in package_metadata["Author-email"]
    assert "Andy (ankunhou)" in package_metadata["Maintainer-email"]
    assert "houankun@hotmail.com" in package_metadata["Maintainer-email"]
