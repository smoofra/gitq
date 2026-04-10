import hashlib
import multiprocessing
import pytest
import os
from pathlib import Path
from contextlib import contextmanager

from gitq.git import split_author, Git, UserError, Commit
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


yara_sample = """commit 6340719ff7030a087407749e7c65ccbb5e84fa1b
tree bc6eb40e56a3537e75f4a3e0dce1ff640e9c4d0d
parent c5630eca73d0be222b5f9535e24fd4ca46a8d45d
author Victor M. Alvarez <vmalvarez@virustotal.com> 1775820206 +0200
committer Victor M. Alvarez <vmalvarez@virustotal.com> 1775820206 +0200
gpgsig -----BEGIN PGP SIGNATURE-----
\x20
 iQJPBAABCAA5FiEEplZEJzz0d/BbCm8bjcwMbKEPUmoFAmnY3a4bFIAAAAAABAAO
 bWFudTIsMi41KzEuMTIsMCwzAAoJEI3MDGyhD1JqNdkP/3dVK4W0DvS6sjuUo6Os
 HOtaRCZxK5CK6MLdnYHOCV09GxfzCZs21e7wlSFADnwjWHk6fsbNxdOshtvwnYRb
 UYwy4ANeYOGRNMb2e/J0kjMqTTccIx3YfJuzzlZ4kUy++e4vkiHTH6lMTvfOiSG1
 t8AURo/pSN0Tq2ytKul2mQYBqKiam30kzr0nAaiYEY7HToQFN7aAIGPWSucXmqlm
 dmUh8HKizTisnnLvHcy+p4CyI2T5GQvinu+BaeNI+KB9VnHTBPqHA9GoR0tWyfTC
 pSg0mNeHUD3EgfW+f9MBC5WELzDx5O1806WZ6E9bF6/spDUBkGH2Zfjli2NSUWQz
 +PIaQyfSUXcjpkxU5pPonwqgY+K60UQ2V2Px94LRJOpcnpHlPzzzEmWD/1b0j3og
 BiO4ed6AJPMwIgGnkwxDAKrFc4UpTAnAiqwdbWh0KvsmWIJAHexiC5UbeR76uz8r
 hh2jGP4S0SUTcQ/l8oubPizEfRkv7TpFCyfmsyRycwvUHPE+gdJye/FakaP18GnB
 um2U3Vpx1f/aX7RMVA3LUN2FqIyOVOjcFxifrCAIpyecOjmSyLEbjAeQxbsP7jHs
 PxfvfXbCAonckI9izh+i9uAHMCWq86bkYDIc29cKXo8sk21QZXDqal1DJfqwmVv1
 3/3QIP3Qv8Pbqj1xOB82ZHTb
 =rVyj
 -----END PGP SIGNATURE-----

    chore: remove unused line from Cargo.toml
"""


def test_gpgsig():
    "test that gpg signatures don't mess up commit parsing"
    assert (  # git log -1  --format=raw | shasum
        hashlib.sha1(yara_sample.encode()).hexdigest()
        == "6ae64024af9a0a5afedbe336b04b4f731b972439"
    )
    c = Commit(log=yara_sample)
    assert c.sha == "6340719ff7030a087407749e7c65ccbb5e84fa1b"
    assert c.title == "chore: remove unused line from Cargo.toml"
