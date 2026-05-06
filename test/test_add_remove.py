import time

import pytest

from .fixtures import Git, repo

_ = repo


@pytest.mark.parametrize("distinct", [True, False])
def test_init_add(repo: Git, distinct: bool):
    repo.c("0")
    root = repo.sha("HEAD")
    repo.s("git branch base HEAD")

    repo.c("common")
    t0 = time.time()
    common = repo.sha("HEAD")
    repo.c("patch")

    # Initialize the queue with base as the baseline
    repo.s("git queue init base")
    assert repo.log() == ["0", "common", "patch", "initialized queue"]
    assert [b.sha for b in repo.q.baselines] == [root]

    # Create a second branch to add as an additional baseline
    repo.s(f"git checkout -q  -b extra {common}")
    if distinct:
        time.sleep(max(0, t0 + 1 - time.time()))  # make sure timestamps are distinct
        repo.s("git commit -q --amend -C HEAD")
        assert repo.sha("HEAD") != common
    else:
        assert repo.sha("HEAD") == common
    repo.c("extra")
    extra = repo.sha("HEAD")

    # Add extra as a second baseline; rebase the queue onto both
    repo.s("git checkout -q master")
    repo.s("git queue add extra")
    assert repo.log() == ["0", "common", "extra", "merged baselines", "patch"]
    assert [b.sha for b in repo.q.baselines] == [root, extra]


def test_add_remove(repo: Git):
    repo.c("0")
    repo.s("git checkout -b a")
    repo.c("a")
    repo.s("git checkout master -b b")
    repo.c("b")
    repo.s("git queue init -b c a")
    repo.c("c")
    assert repo.log() == ["0", "a", "baseline", "c"]
    repo.s("git queue add b")
    assert repo.log() == ["0", "a", "b", "merged baselines", "c"]
    repo.s("git queue remove a")
    assert repo.log() == ["0", "b", "baseline", "c"]
    repo.s("git queue rebase --remove b --add a")
    assert repo.log() == ["0", "a", "baseline", "c"]
