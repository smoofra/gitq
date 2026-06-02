from textwrap import dedent
from io import StringIO

import yaml

from gitq.git import Sha
from gitq.queue import QueueFile, Loader, Dumper

from .fixtures import Git, repo

_ = repo


def test_yaml() -> None:

    qf: QueueFile = yaml.load(StringIO("{}"), Loader=Loader)
    assert qf.description is None
    assert qf.baselines == []

    y = """
        title: my branch
        description: |
          This is a branch.
          Foo Bar Baz
        baselines:
        - sha: abc1234
        - sha: abcdef
          ref: bar
          remote: https://example.com/project.git
    """

    qf = yaml.load(StringIO(dedent(y)), Loader=Loader)
    assert qf.description == "This is a branch.\nFoo Bar Baz\n"
    foo, bar = qf.baselines
    assert foo.ref is None
    assert foo.remote is None
    assert foo.sha == "abc1234"
    assert bar.ref == "bar"
    assert bar.remote == "https://example.com/project.git"
    assert bar.sha == "abcdef"
    assert all(isinstance(b.sha, Sha) for b in qf.baselines)

    with StringIO() as s:
        yaml.dump(qf, s, Dumper=Dumper)
        assert dedent(y).strip() == s.getvalue().strip()

    try:
        qf = yaml.load(StringIO("lol: wtf"), Loader=Loader)
    except Exception:
        pass
    else:
        raise Exception("parse should have failed")


def test_tidy(repo: Git):
    repo.c("0")
    repo.s("git checkout -b base")
    repo.c("base1")
    repo.s("git queue init -b q base")
    repo.c("patch1")

    base_sha = repo.sha("refs/heads/base")

    # Overwrite the queuefile with valid but non-normalized YAML
    # After tidy the content should be re-serialized using the custom Dumper.
    queuefile = repo / ".git-queue"
    with open(queuefile, "w") as f:
        f.write(f"baselines:\n- ref: refs/heads/base\n  sha: {base_sha}\n")

    with open(queuefile) as f:
        repr0 = f.read()
        f.seek(0)
        qf0: QueueFile = yaml.load(f, Loader=Loader)

    repo.s("git queue tidy")

    with open(queuefile) as f:
        repr1 = f.read()
        f.seek(0)
        qf1: QueueFile = yaml.load(f, Loader=Loader)

    assert repr0 != repr1
    assert qf0 == qf1


def test_commit(repo: Git):
    repo.c("0")
    repo.s("git branch -m base")
    repo.s("git checkout -q -b q")
    repo.c("a")
    repo.c("b")
    repo.s("git queue init base")
    repo.s("git queue rebase")

    repo.s("git queue commit -m 1 -b mq")

    repo.s("git checkout -q -b base2 base")
    repo.c("c")

    repo.s("git checkout -q q")
    repo.s("git queue add base2")

    repo.s("git config set branch.q.gitq-historiography refs/heads/mq")
    repo.s("git queue commit -m 2")

    changed = {
        x.strip() for x in repo("show", "--format=", "--name-only", "mq").strip().splitlines()
    }

    assert changed == {
        ".git-queue",
        "c",
        "commits/0-baseline.patch",
        "commits/0-merged-baselines.patch",
        "commits/1-a.patch",
        "commits/2-b.patch",
    }

    diff_q_mq = {x.strip() for x in repo("diff", "--name-only", "q", "mq").strip().splitlines()}

    assert diff_q_mq == {
        ".git-queue",
        "commits/0-merged-baselines.patch",
        "commits/1-a.patch",
        "commits/2-b.patch",
    }


def test_commit_preserves_unapplied_patches(repo: Git):
    repo.c("a")
    repo.s("git branch base HEAD")
    repo.c("b")
    repo.c("c")
    repo.s("git queue init base")
    repo.s("git checkout -q base")
    repo.c("B", filename="b")
    repo.s("git checkout -q master")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    repo.s("git queue save-patch")
    assert repo.qf.unapplied_patches == ["patches/0-b.patch"]

    repo.s("git queue commit -m v1 -b mq")

    committed = set(repo.so("git show --format= --name-only mq").splitlines())
    assert "patches/0-b.patch" in committed

    repo.s("git checkout -q mq")
    assert repo.qf.unapplied_patches == ["patches/0-b.patch"]


def test_edit(repo: Git):
    repo.c("0")
    repo.s("git branch -m base")
    repo.s("git checkout -q -b q")
    repo.c("a")
    repo.c("b")
    repo.s("git queue init base")
    repo.s("git queue rebase")

    q_sha = repo.sha("HEAD")

    repo.s("git queue commit -m 1 -b mq")

    # edit with explicit --branch finds queue branch `q`
    repo.s("git checkout -q mq")
    repo.s("git queue edit")
    assert repo.branch() == "q"
    assert repo.sha("HEAD") == q_sha
    assert repo("config", "branch.q.gitq-historiography").strip() == "refs/heads/mq"

    # edit with explicit --branch creates the queue branch and links config
    repo.s("git checkout -q mq")
    repo.s("git branch -D q")
    repo.s("git queue edit -b q2")
    assert repo.branch() == "q2"
    assert repo.sha("HEAD") == q_sha
    assert repo("config", "branch.q2.gitq-historiography").strip() == "refs/heads/mq"

    repo.c("c")
    repo.s("git queue commit -m 2")
    q_sha2 = repo.sha("HEAD")

    # edit without --branch finds the queue branch `q2`
    repo.s("git checkout -q mq")
    repo.s("git queue edit")
    assert repo.branch() == "q2"
    assert repo.sha("HEAD") == q_sha2

    # edit without --branch finds the queue branch `q2`
    repo.s("git checkout -q mq")
    repo.s("git branch -D q2")
    repo.s("git queue edit", check_error="No queue branch found")
    repo.s("git queue edit -b q3")
    assert repo.branch() == "q3"
    assert repo.sha("HEAD") == q_sha2

    repo.s("git checkout -q q3")
    repo.c("x")
    repo.s("git checkout -q mq")
    repo.s("git queue edit", check_error="diverged")
