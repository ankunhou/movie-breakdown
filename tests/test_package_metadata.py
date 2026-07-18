from importlib.metadata import metadata, version

from movie_breakdown import __version__


def test_public_version_comes_from_installed_package_metadata() -> None:
    assert __version__ == version("movie-breakdown")

    package_metadata = metadata("movie-breakdown")
    assert "Andy (ankunhou)" in package_metadata["Author-email"]
    assert "houankun@hotmail.com" in package_metadata["Author-email"]
    assert "Andy (ankunhou)" in package_metadata["Maintainer-email"]
    assert "houankun@hotmail.com" in package_metadata["Maintainer-email"]

    project_urls = package_metadata.get_all("Project-URL") or []
    assert "Repository, https://github.com/ankunhou/movie-breakdown.git" in project_urls
    assert "Issues, https://github.com/ankunhou/movie-breakdown/issues" in project_urls
