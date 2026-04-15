import pytest
from .fixtures import Git, repo

_ = repo


def test_rebase(repo: Git):
    repo.c("a")
    repo.s("git branch base HEAD")
    base0 = repo.rev_parse("HEAD")

    repo.c("b")
    repo.s("git queue init base")

    repo.s("git checkout -q base")
    repo.w("a", "A")
    repo.s("git commit -qa --amend -m A")
    base1 = repo.rev_parse("HEAD")

    repo.s("git checkout -q master")
    assert repo.log() == ["a", "b", "initialized queue"]
    assert [b.sha for b in repo.q.baselines] == [base0]

    repo.s("git queue rebase")
    assert repo.log() == ["A", "baseline", "b"]
    assert [b.sha for b in repo.q.baselines] == [base1]


def test_two_baselines(repo: Git):

    repo.s("git commit --allow-empty -m0")

    repo.s("git checkout -b a master")
    repo.w("a", "a")
    repo.s("git add a && git commit -m a")
    a = repo.rev_parse("HEAD")

    repo.s("git checkout -b b master")
    repo.w("b", "b")
    repo.s("git add b && git commit -m b")
    b = repo.rev_parse("HEAD")

    repo.s("git checkout -b c master")
    repo.s("git queue init a b")
    repo.w("c", "c")
    repo.s("git add c && git commit -m c")
    assert [b.sha for b in repo.q.baselines] == [a, b]

    repo.s("git queue rebase")
    assert repo.log() == ["0", "a", "b", "merged baselines", "c"]

    repo.s("git checkout a")
    repo.w("a", "A")
    repo.s("git commit -a  -m A")
    A = repo.rev_parse("HEAD")

    repo.s("git checkout c")
    repo.s("git queue rebase")
    assert repo.log() == ["0", "a", "A", "b", "merged baselines", "c"]
    assert [b.sha for b in repo.q.baselines] == [A, b]


def test_rebase_merge(repo: Git):
    "Test that rebase can skip over non-conflicted merges"

    repo.w("a", "a")
    repo.s("git add a && git commit -q -m a")
    repo.s("git branch base HEAD")

    repo.s("git queue init base")
    q0 = repo.rev_parse("HEAD")

    repo.w("b", "b")
    repo.s("git add b && git commit -q -m b")

    repo.s(f"git checkout -q {q0}")
    repo.w("c", "c")
    repo.s("git add c && git commit -q -m c")

    repo.s("git checkout -q master")
    repo.s("git merge HEAD@{1} -qm merge")

    repo.s("git checkout -q base")
    repo.w("a", "A")
    repo.s("git commit -qa --amend -m A")
    base1 = repo.rev_parse("HEAD")

    repo.s("git checkout -q master")
    repo.s("git queue rebase")

    assert repo.log() == ["A", "baseline", "b", "c"]
    assert [b.sha for b in repo.q.baselines] == [base1]


def test_rebase_conflicted_merge(repo: Git):
    "Test that rebase prints an error when the queue contains a merge with conflicts"

    repo.c("a")
    repo.s("git branch base HEAD")
    repo.s("git queue init base")
    q0 = repo.rev_parse("HEAD")

    repo.c("b")

    repo.s(f"git checkout -q {q0}")
    repo.c("b", content="B")
    other = repo.rev_parse("HEAD")

    repo.s("git checkout -q master")

    # create a merge with resolved conflicts
    repo.s(f"git merge --no-commit -q {other}; [[ $? = 1 ]]")
    repo.s("echo bB>b && git add b && git commit -q -m merge")

    repo.s("git queue rebase | grep 'rebasing merges is not implemented yet'")


def test_rebase_edited_merge(repo: Git):
    "Test that rebase prints an error when the queue contains a merge manual edits"

    repo.c("a")
    repo.s("git branch base HEAD")
    repo.s("git queue init base")
    q0 = repo.rev_parse("HEAD")

    repo.c("b")

    repo.s(f"git checkout -q {q0}")
    repo.c("c")
    other = repo.rev_parse("HEAD")

    repo.s("git checkout -q master")

    # create a merge with manual edits
    repo.s(f"git merge -q {other} -m merge")
    repo.w("c", "C")
    repo.s("git commit -q -a --amend -C HEAD")

    repo.s("git queue rebase | grep 'rebasing merges is not implemented yet'")


@pytest.mark.parametrize("case", ["normal", "wrong_tool", "abort"])
def test_rebase_conflict(repo: Git, case):
    repo.c("a")
    repo.s("git branch base HEAD")

    repo.c("b")
    repo.c("c")

    repo.s("git queue init base")

    repo.s("git checkout -q base")
    repo.w("a", "A")
    repo.w("b", "")
    repo.w("c", "")
    repo.s("git add a b c")
    repo.s("git commit -a --amend -m A -q")
    base1 = repo.rev_parse("HEAD")

    repo.s("git checkout -q master")
    sha = repo.rev_parse("HEAD")

    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"b"}

    repo.w("b", "b")
    repo.s("git add b")
    repo.s("git queue continue; [[ $? = 2 ]]")

    assert repo.unmerged() == {"c"}
    repo.w("c", "c")
    repo.s("git add c")
    if case == "normal":
        repo.s("git queue continue")
    elif case == "wrong_tool":
        # test calling continue from the wrong tool
        repo.s("git edit --continue")
    else:
        repo.s("git edit --abort")
        assert repo.rev_parse("HEAD") == sha
        assert repo.head() == "refs/heads/master"
        return

    assert repo.log() == ["A", "baseline", "b", "c"]
    assert [b.sha for b in repo.q.baselines] == [base1]
    assert set(repo("diff", "--name-only", sha, "HEAD").splitlines()) == {"a", ".git-queue"}


def test_rebase_cherry(repo: Git):
    """
    Test that rebase skips commits that have been cherry-picked down into
    baseline.
    """

    repo.s("echo a >a && git add a && git commit -m a")
    repo.s("git branch base HEAD")

    repo.s("git queue init -b q base")

    repo.s("echo b >b && git add b && git commit -m b")
    assert repo.log() == ["a", "baseline", "b"]

    repo.s("git checkout base")
    repo.s("git cherry-pick q")

    repo.s("git checkout q")

    repo.s("echo d >d && git add d && git commit -m d")
    assert repo.log() == ["a", "baseline", "b", "d"]

    repo.s("git queue rebase")
    assert repo.log() == ["a", "b", "baseline", "d"]


def test_recursive_rebase(repo: Git):
    """
    When bar's baseline is foo (a local queue branch), rebasing bar should
    rebase foo first, then rebase bar on top of the updated foo.
    """
    repo.c("0")

    # Create base branch and foo as a queue on it
    repo.s("git checkout -b base")
    repo.c("base1")
    repo.s("git queue init -b foo base")
    repo.c("foo1")
    repo.c("foo2")
    assert repo.log() == ["0", "base1", "baseline", "foo1", "foo2"]

    # Create bar as a queue with foo as its baseline
    repo.s("git queue init -b bar foo")
    repo.c("bar1")
    repo.c("bar2")
    assert repo.log() == ["0", "base1", "baseline", "foo1", "foo2", "baseline", "bar1", "bar2"]

    # Update base so foo (and transitively bar) is out of date
    repo.s("git checkout base")
    repo.c("base2")

    # Rebase bar — should rebase foo first, then bar
    repo.s("git checkout bar")

    repo.s("git queue rebase")

    # foo should have been rebased to include base2
    repo.s("git checkout foo")
    assert repo.log() == ["0", "base1", "base2", "baseline", "foo1", "foo2"]

    # bar should have been rebased onto the new foo
    repo.s("git checkout bar")
    assert repo.log() == [
        "0",
        "base1",
        "base2",
        "baseline",
        "foo1",
        "foo2",
        "baseline",
        "bar1",
        "bar2",
    ]


def test_recursive_rebase_conflict(repo: Git):
    """
    When bar's baseline is foo, and rebasing foo encounters a merge conflict,
    the user resolves it and continues, after which bar is rebased onto the
    updated foo.
    """
    repo.c("0")

    # Create base branch and foo as a queue on it.
    # foo1 modifies "shared" — this will conflict with base2.
    repo.s("git checkout -q -b base")
    repo.c("base1")
    repo.s("git queue init -b foo base")
    repo.w("shared", "foo version")
    repo.s("git add shared && git commit -q -m foo1")
    repo.c("foo2")
    assert repo.log() == ["0", "base1", "baseline", "foo1", "foo2"]

    # Create bar as a queue with foo as its baseline
    repo.s("git queue init -b bar foo")
    repo.c("bar1")
    repo.c("bar2")
    assert repo.log() == ["0", "base1", "baseline", "foo1", "foo2", "baseline", "bar1", "bar2"]

    # Update base with a commit that conflicts with foo1's "shared" file
    repo.s("git checkout -q base")
    repo.w("shared", "base version")
    repo.s("git add shared && git commit -q -m base2")

    # Rebase bar — should rebase foo first, hitting a conflict on foo1
    repo.s("git checkout -q bar")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    # Resolve the conflict; one continue should finish both foo and bar rebases
    repo.w("shared", "resolved")
    repo.s("git add shared")
    repo.s("git queue continue")

    # foo should have been rebased to include base2
    repo.s("git checkout -q foo")
    assert repo.log() == ["0", "base1", "base2", "baseline", "foo1", "foo2"]

    # bar should have been rebased onto the new foo
    repo.s("git checkout -q bar")
    assert repo.log() == [
        "0",
        "base1",
        "base2",
        "baseline",
        "foo1",
        "foo2",
        "baseline",
        "bar1",
        "bar2",
    ]


def test_rebase2_cherry(repo: Git):
    """
    Test that rebase skips commits that have been cherry-picked down into
    one of the baselines.
    """

    repo.s("git commit --allow-empty -m 0")

    repo.s("git checkout -b base-a")
    repo.s("echo a >a && git add a && git commit -m a")

    repo.s("git checkout master -b base-b")
    repo.s("echo b >b && git add b && git commit -m b")

    repo.s("git queue init -b q base-a base-b")

    repo.s("echo c >c && git add c && git commit -m c")
    assert repo.log() == ["0", "a", "b", "merged baselines", "c"]

    repo.s("git checkout base-a")
    repo.s("git cherry-pick q")

    repo.s("git checkout q")

    repo.s("echo d >d && git add d && git commit -m d")
    assert repo.log() == ["0", "a", "b", "merged baselines", "c", "d"]

    repo.s("git queue rebase")

    assert repo.log() == ["0", "a", "c", "b", "merged baselines", "d"]


def test_queuefile_conflict(repo: Git):
    "Test that git-queue can resolve conflicts on .git-queue automatically"
    repo.c("0")

    # Two independent base branches so each queue records a different baseline sha
    repo.s("git checkout -qb base_a")
    repo.c("a")
    repo.s("git checkout -q master")
    repo.s("git checkout -qb base_b")
    repo.c("b")
    repo.s("git checkout -q master")

    # foo's .git-queue records sha of "a"; bar's records sha of "b"
    repo.s("git queue init -b foo base_a")
    repo.s("git queue init -b bar base_b")

    # Create baz with foo as its baseline
    repo.s("git queue init -b baz foo")
    repo.c("baz patch")

    repo.s("git queue add bar")
    assert repo.log() == ["0", "a", "baseline", "b", "baseline", "merged baselines", "baz patch"]


def test_rebase_independent_baselines(repo: Git):
    "see the long comment in find_git_cherry_limit"
    repo.c("0")

    # branch_a
    repo.s("git checkout -b branch_a")
    repo.c("a")
    sha_a = repo.rev_parse("HEAD")
    repo.s("git commit --allow-empty -m $'baseline\\n\\nTool: gitq'")
    repo.c("patch_a")

    # branch_b
    repo.s("git checkout master")
    repo.s("git checkout -b branch_b")
    repo.c("b")
    sha_b = repo.rev_parse("HEAD")
    repo.s("git commit --allow-empty -m $'baseline\\n\\nTool: gitq'")
    repo.c("patch_b")

    # make a weird queue containing merges and independent baselines
    repo.s("git merge branch_a --no-ff -m 'merge two independent branches'")
    repo.w(".git-queue", f"baselines:\n- sha: {sha_a}\n- sha: {sha_b}\n")
    repo.s("git add .git-queue && git commit -m 'setup queue'")

    # rebase should still work!
    repo.s("git queue rebase")
    assert repo.log() == ["0", "a", "b", "merged baselines", "patch_b", "patch_a"]


def test_rebase_merge_limit(repo: Git):
    """
    Test that rebase can deal with a non-conflicted merge at the bottom of the queue.
    This is a special case in find_git_cherry_limit
    """
    repo.c("0")

    repo.s("git checkout -qb a master")
    repo.c("a")

    repo.s("git checkout -qb b master")
    repo.c("b")

    # make a regular merge commit
    repo.s("git checkout -qb work a")
    repo.s("git merge -q b --no-ff -m 'merge b'")

    # make a weird queue starting at the merge
    repo.s("git queue init a b")
    repo.c("patch")

    repo.s("git queue rebase")
    assert repo.log() == ["0", "a", "b", "merged baselines", "patch"]


def test_queuefile_conflict_3way(repo: Git):
    "Test .git-queue conflict resolution when octopus merge fails"
    repo.c("0")

    repo.s("git checkout -qb base_a")
    repo.c("a")
    repo.s("git checkout -q master")
    repo.s("git checkout -qb base_b")
    repo.c("b")
    repo.s("git checkout -q master")
    repo.s("git checkout -qb base_c")
    repo.c("c")
    repo.s("git checkout -q master")

    # Three queues, each recording a different baseline sha
    repo.s("git queue init -b foo base_a")
    repo.s("git queue init -b bar base_b")
    repo.s("git queue init -b qux base_c")

    # Create baz with foo as its baseline
    repo.s("git queue init -b baz foo")
    repo.c("baz patch")

    # Adding two baselines at once: octopus merge refuses the 3-way .git-queue
    # conflict without setting MERGE_HEAD, so the code falls through to the
    # one-at-a-time loop which resolves each .git-queue conflict individually.
    repo.s("git queue add bar qux")
    assert repo.log() == [
        "0",
        "a",
        "baseline",
        "b",
        "baseline",
        "merged baselines",
        "c",
        "baseline",
        "merged baselines",
        "baz patch",
    ]


@pytest.mark.parametrize("commit", [True, False])
def test_merge_baseline_conflict(repo: Git, commit: bool):
    "Test conflicting baselines and user-merges."
    repo.c("0")

    # Two branches each modifying a different file — no conflict initially
    repo.s("git checkout -qb base_a master")
    repo.c("a")

    repo.s("git checkout -qb base_b master")
    repo.c("b")

    # Queue with both baselines — merges cleanly
    repo.s("git checkout -q master")
    repo.s("git queue init -b q base_a base_b")
    repo.c("patch")

    # Update both baselines to conflict on the same file
    repo.s("git checkout -q base_a")
    repo.w("shared", "version a")
    repo.s("git add shared && git commit -qm a2")

    repo.s("git checkout -q base_b")
    repo.w("shared", "version b")
    repo.s("git add shared && git commit -qm b2")

    # Rebase should suspend: merging the two updated baselines conflicts on "shared"
    repo.s("git checkout -q q")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    # Resolve the conflict and continue
    repo.w("shared", "merged")
    repo.s("git add shared")
    if commit:
        repo.s("git commit -q --no-edit")
    repo.s("git queue continue")

    # Rebase works now!
    assert repo.log() == [
        "0",
        "a",
        "a2",
        "b",
        "b2",
        "resolved conflicts",
        "merged baselines",
        "patch",
    ]

    # Rebase continues to work using the same user-merge commit
    repo.c("patch2")
    repo.s("git queue rebase")
    assert repo.log() == [
        "0",
        "a",
        "a2",
        "b",
        "b2",
        "resolved conflicts",
        "merged baselines",
        "patch",
        "patch2",
    ]

    # Rebase still works if you add more commits to the baselines
    repo.s("git checkout -q base_a")
    repo.c("a3")
    repo.s("git checkout -q q")
    repo.s("git queue rebase")
    assert repo.log() == [
        "0",
        "a",
        "a2",
        "a3",
        "b",
        "b2",
        "resolved conflicts",
        "merged baselines",
        "patch",
        "patch2",
    ]
