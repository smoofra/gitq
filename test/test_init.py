from .fixtures import Git, repo
from gitq.queue import Queue

_ = repo


def test_init(repo: Git):
    repo.w("a", "a")
    repo.s("git add a && git commit -m a")
    repo.s("git branch base HEAD")
    repo.s("git queue init base")
    assert repo.log() == ["a", "initialized queue"]
    assert [b.ref for b in Queue(repo).qf.baselines] == ["refs/heads/base"]
    repo.s("git queue rebase")
    assert repo.log() == ["a", "baseline"]
    assert [b.ref for b in Queue(repo).qf.baselines] == ["refs/heads/base"]


def test_init_new_branch(repo: Git):
    repo.w("a", "a")
    repo.s("git add a && git commit -m a")
    repo.s("git branch base HEAD")
    repo.s("git queue init -b foo base")
    assert repo.head() == "refs/heads/foo"
    assert repo.log() == ["a", "baseline"]
    assert [b.ref for b in Queue(repo).qf.baselines] == ["refs/heads/base"]
