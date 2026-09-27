import os

from common import ROOT, resolve_repo_path


def test_resolve_repo_path_preserves_http_source_url():
    url = "https://example.invalid/video.mp4"

    assert resolve_repo_path(url) == url


def test_resolve_repo_path_resolves_repo_relative_source():
    assert resolve_repo_path("input/video.mp4") == os.path.join(ROOT, "input/video.mp4")

def test_to_repo_relative_preserves_http_source_url():
    from common import to_repo_relative

    url = "https://example.invalid/video.mp4"

    assert to_repo_relative(url) == url
