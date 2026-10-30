from textwrap import dedent
from io import StringIO

import yaml

from gitq.git import Sha
from gitq.queue import QueueFile, Loader, Dumper, read_patch_origin

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
    assert not any(True for x in repo.q.commits_directory.glob("*"))

    repo.s("git checkout -q -b base2 base")
    repo.c("c")

    repo.s("git checkout -q q")
    repo.s("git queue add base2")

    repo.s("git config set branch.q.gitq-historiography refs/heads/mq")
    repo.s("git queue commit -m 2")
    assert not any(True for x in repo.q.commits_directory.glob("*"))

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
    assert not any(True for x in repo.q.commits_directory.glob("*"))

    committed = set(repo.so("git show --format= --name-only mq").splitlines())
    assert "patches/0-b.patch" in committed

    repo.s("git checkout -q mq")
    assert repo.qf.unapplied_patches == ["patches/0-b.patch"]

    repo.s("git checkout -q master")
    repo.s("git queue rebase")

    assert repo.qf.unapplied_patches == ["patches/0-b.patch"]
    assert repo.log() == ["a", "B", "baseline", "c"]

    repo.s("git queue commit -m rebased")
    assert not any(True for x in repo.q.commits_directory.glob("*"))


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


def test_commit_binary_file(repo: Git):
    repo.c("0")
    repo.s("git branch -m base")
    repo.s("git checkout -q -b q")
    repo.c("a")

    (repo / "blob.bin").write_bytes(bytes(range(256)))
    repo("add", "blob.bin")
    repo("commit", "-q", "-m", "add binary")

    (repo / "blob.bin").write_bytes(bytes(reversed(range(256))) * 4)
    repo("add", "blob.bin")
    repo("commit", "-q", "-m", "change binary")

    repo.s("git queue init base")
    repo.s("git queue rebase")
    q_sha = repo.sha("HEAD")

    # the round-trip check inside `commit` is what would fail without --binary
    repo.s("git queue commit -m 1 -b mq")

    repo.s("git checkout -q mq")
    assert "GIT binary patch" in repo.r("commits/2-add-binary.patch")

    repo.s("git queue edit")
    assert repo.branch() == "q"
    assert repo.sha("HEAD") == q_sha
    assert (repo / "blob.bin").read_bytes() == bytes(reversed(range(256))) * 4


def test_status_shows_conflicts(repo: Git):
    repo.c("a")
    repo.s("git branch base HEAD")
    repo.c("b")
    repo.s("git queue init base")
    repo.s("git checkout -q base")
    repo.c("B", filename="b")
    repo.s("git checkout -q master")
    suspended = repo.so("git queue rebase; [[ $? = 2 ]]")
    assert "Unmerged paths:" in suspended
    assert "both added:  b" in suspended

    out = repo.so("git queue status")
    assert "Unmerged paths:" in out
    assert "both added:  b" in out


def test_apply_status_names_the_patch(repo: Git):
    repo.c("a")
    repo.s("git branch base HEAD")
    repo.c("make b say b", filename="b", content="b")
    original = repo.so("git rev-parse --short HEAD")
    repo.s("git queue init base")
    repo.s("git checkout -q base")
    repo.c("B", filename="b")
    repo.s("git checkout -q master")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    repo.s("git queue save-patch")

    patch = "patches/0-make-b-say-b.patch"
    assert repo.qf.unapplied_patches == [patch]

    repo.s("git queue apply; [[ $? = 2 ]]")
    out = repo.so("git queue status")
    assert f" * Applying {patch} (from {original})" in out


def test_patch_origin_of_a_vanished_commit(repo: Git):
    repo.c("a")
    repo.s("git queue init HEAD~0")
    missing = "0" * 40
    (repo / "fake.patch").write_text(
        f"From {missing} Mon Sep 17 00:00:00 2001\n"
        "From: A U Thor <author@example.com>\n"
        "Subject: [PATCH] a title\n"
        "\n"
        "body\n"
    )
    assert read_patch_origin(repo / "fake.patch") == missing
    assert repo.q.patch_origin("fake.patch") == f"{missing[:10]}, not in this repo"


def test_commit_message_from_a_file(repo: Git):
    repo.c("0")
    repo.s("git branch -m base")
    repo.s("git checkout -q -b q")
    repo.c("a")
    repo.s("git queue init base")
    repo.s("git queue rebase")

    repo.s("mkdir sub")
    repo.w("sub/msg.txt", "from a file\n\nwith a body")
    # relative to the caller's directory, not the top level
    repo.s("cd sub && git queue commit -F msg.txt -b mq")
    assert repo.commit("mq").message == "from a file\n\nwith a body\n"

    # nothing has changed, so there is nothing to commit
    repo.s("git queue commit -m again", check_error="queue has not changed")
    assert repo.branch() == "q"


def test_drop_patch(repo: Git):
    repo.c("a")
    repo.s("git branch base HEAD")
    repo.c("b")
    repo.c("c")
    repo.s("git queue init base")

    # make both b and c conflict with the baseline
    repo.s("git checkout -q base")
    repo.c("B", filename="b")
    repo.c("C", filename="c")
    repo.s("git checkout -q master")

    repo.s("git queue rebase; [[ $? = 2 ]]")
    repo.s("git queue save-patch; [[ $? = 2 ]]")
    repo.s("git queue save-patch")
    assert repo.qf.unapplied_patches == ["patches/0-b.patch", "patches/1-c.patch"]

    repo.s("git queue drop patches/1-c.patch")
    assert repo.qf.unapplied_patches == ["patches/0-b.patch"]
    assert not (repo / "patches/1-c.patch").exists()
    assert (repo / "patches/0-b.patch").exists()
    assert repo.commit("HEAD").message.startswith("drop patch: patches/1-c.patch")

    repo.s("git queue drop nope.patch", check_error="Not an unapplied patch")

    # no argument drops the first one
    repo.s("git queue drop")
    assert repo.qf.unapplied_patches is None
    assert not (repo / "patches/0-b.patch").exists()

    repo.s("git queue drop", check_error="No unapplied patches")


def conflicting_queue(repo: Git, *, bare: str = "") -> None:
    "A queue whose first patch conflicts with an upstream change to the baseline."
    repo.w("f", "one\ntwo\nthree")
    repo.s("git add .")
    repo.s("git commit -q -m base0")
    repo.s("git branch base HEAD")
    repo.w("f", "one\nTWO\nthree")
    repo.s("git add .")
    repo.s("git commit -q -m 'patch one'")
    repo.c("p2")
    repo.s(f"git queue init {bare} base")

    repo.s("git checkout -q base")
    repo.w("f", "ONE\ntwo\nthree\nfour")
    repo.s("git add .")
    repo.s("git commit -q -m upstream")
    repo.s("git checkout -q master")


def test_queue_defer(repo: Git):
    conflicting_queue(repo)
    before = repo.so("git show --format= :/'patch one'")

    repo.s("git queue rebase; [[ $? = 2 ]]")
    repo.s("git queue defer")

    assert repo.log() == [
        "base0",
        "upstream",
        "baseline",
        "📌 defer conflict with patch one",
        "patch one",
        "p2",
    ]
    assert repo.so("git show --format= :/'patch one'") == before

    # the rebase's own bookkeeping is not reverted: the queuefile still names
    # the baseline we just rebased onto
    assert repo.qf.baselines[0].sha == repo.sha(":/upstream")

    # and the deferred commit is an ordinary patch, not a tool commit
    repo.s("git checkout -q base")
    repo.c("later upstream")
    repo.s("git checkout -q master")
    repo.s("git queue rebase")
    assert "📌 defer conflict with patch one" in repo.log()


def test_queue_defer_bare(repo: Git):
    "a bare queue keeps its metadata in config, so there is no queuefile to keep"
    conflicting_queue(repo, bare="--bare")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    repo.s("git queue defer")

    assert repo.log() == [
        "base0",
        "upstream",
        "📌 defer conflict with patch one",
        "patch one",
        "p2",
    ]
    assert repo.qf.baselines[0].sha == repo.sha(":/upstream")
    assert not (repo / ".git-queue").exists()


def test_queue_defer_keeps_a_patch_saved_earlier(repo: Git):
    "save-patch and defer in the same rebase: the patch file must survive"
    repo.w("f", "one\ntwo\nthree")
    repo.w("g", "g1")
    repo.s("git add .")
    repo.s("git commit -q -m base0")
    repo.s("git branch base HEAD")
    repo.w("f", "one\nTWO\nthree")
    repo.s("git add .")
    repo.s("git commit -q -m 'patch one'")
    repo.w("g", "G1")
    repo.s("git add .")
    repo.s("git commit -q -m 'patch two'")
    repo.s("git queue init base")

    # upstream conflicts with both patches
    repo.s("git checkout -q base")
    repo.w("f", "ONE\ntwo\nthree\nfour")
    repo.w("g", "gX")
    repo.s("git add .")
    repo.s("git commit -q -m upstream")
    repo.s("git checkout -q master")

    repo.s("git queue rebase; [[ $? = 2 ]]")
    repo.s("git queue save-patch; [[ $? = 2 ]]")
    repo.s("git queue defer")

    assert repo.qf.unapplied_patches == ["patches/0-patch-one.patch"]
    assert (repo / "patches/0-patch-one.patch").exists()
    assert repo.qf.baselines[0].sha == repo.sha(":/upstream")
    assert repo.log()[-2:] == ["📌 defer conflict with patch two", "patch two"]


def test_queue_defer_pins_the_tree_on_a_later_rebase(repo: Git):
    conflicting_queue(repo)
    repo.s("git queue rebase; [[ $? = 2 ]]")
    repo.s("git queue defer")
    patch = repo.so("git show --format= :/'patch one'")
    before = repo.sha("HEAD")
    assert repo.commit(":/defer").gitq_type == "defer"

    # upstream moves again, in a way that would conflict all over again
    repo.s("git checkout -q base")
    repo.w("f", "ONE\nTWO\nTHREE\nfour")
    repo.s("git add .")
    repo.s("git commit -q -m upstream2")
    repo.s("git checkout -q master")

    repo.s("git queue rebase")

    # the defer commit is still a patch, carried forward as a tree rather than
    # a delta, so nothing above it had anything to merge
    assert repo.log()[-3:] == ["📌 defer conflict with patch one", "patch one", "p2"]
    assert repo.so("git show --format= :/'patch one'") == patch

    # the only thing the new baseline got to change is the queue's own
    # bookkeeping; the pinned tree carried everything else through untouched
    assert repo.so(f"git diff --name-only {before} HEAD") == ".git-queue"
    assert repo.qf.baselines[0].sha == repo.sha(":/upstream2")
