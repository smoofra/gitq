import multiprocessing
import pytest
import os
from pathlib import Path
from contextlib import contextmanager

from gitq.git import split_author, Git, UserError
from .fixtures import repo

_ = repo


def test_split():

    name, email, date = split_author("Lawrence D'Anna <larry@elder-gods.org> 1760365949 -0400")
    assert name == "Lawrence D'Anna"
    assert email == "larry@elder-gods.org"
    assert date == "1760365949 -0400"

    name, email, date = split_author("Lawrence D'Anna <larry@elder-gods.org> 1760365949 +0400")
    assert name == "Lawrence D'Anna"
    assert email == "larry@elder-gods.org"
    assert date == "1760365949 +0400"

    name, email, date = split_author("Lawrence D'Anna <larry@elder-gods.org> 1760365949 0000")
    assert name == "Lawrence D'Anna"
    assert email == "larry@elder-gods.org"
    assert date == "1760365949 0000"


@pytest.mark.parametrize("sub", [".", "sub", "sub/sub"])
@pytest.mark.parametrize(
    ["cwd", "rel"],
    [
        ["other", False],
        ["repo", True],
        ["repo", False],
        ["parent", True],
        ["parent", False],
        ["sub", True],
        ["sub", False],
    ],
)
def test_gitdir(repo, sub: str, cwd: str, rel: bool):
    "test .gitdir and .directory are initialized correctly"
    if "PYTEST_XDIST_WORKER" in os.environ:
        ctx = multiprocessing.get_context("spawn")
        p = ctx.Process(target=_test_gitdir, args=(str(repo.path), sub, cwd, rel))
        p.start()
        p.join()
        assert p.exitcode == 0
    else:
        _test_gitdir(repo.path, sub, cwd, rel)


def _test_gitdir(repo: Path | str, sub: str, cwd: str, rel: bool):
    repo = Path(repo)
    subdir = repo / sub
    os.makedirs(subdir, exist_ok=True)

    if cwd == "repo":
        pwd = repo
    elif cwd == "parent":
        pwd = repo.parent
    elif cwd == "sub":
        pwd = subdir
    elif cwd == "other":
        pwd = Path("/etc")
    else:
        raise NotImplementedError

    with chdir(pwd):
        git = Git(subdir.relative_to(pwd) if rel else subdir)
        assert git.directory == repo
        assert git.gitdir == repo / ".git"


@contextmanager
def chdir(d: str | Path):
    previous = os.getcwd()
    try:
        os.chdir(d)
        yield
    finally:
        os.chdir(previous)


def test_not_a_repo():
    try:
        Git("/")
    except UserError as e:
        assert "not a git repository" in str(e)
