from .fixtures import Git, repo

_ = repo


def test_edit_head(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    assert repo.log() == ["0", "a"]

    repo.s("git edit HEAD; [[ $? = 2 ]]")

    # amend the HEAD commit
    repo.w("a", "AAA")
    repo.s("git add .")
    repo.s("git commit --amend -q --no-edit")

    repo.s("git edit --continue")
    assert repo.log() == ["0", "a"]
    assert (repo / "a").read_text() == "AAA\n"


def test_edit_middle(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("b", "bbb")
    repo.s("git add .")
    repo.s("git commit -q -m b")
    repo.w("c", "ccc")
    repo.s("git add .")
    repo.s("git commit -q -m c")
    assert repo.log() == ["0", "b", "c"]

    sha = repo.sha("HEAD")
    repo.s("git edit :/b; [[ $? = 2 ]]")

    # amend the commit we checked out to
    repo.w("b", "BBB")
    repo.s("git add .")
    repo.s("git commit --amend -q --no-edit")

    repo.s("git edit --continue")
    assert repo.log() == ["0", "b", "c"]
    assert (repo / "b").read_text() == "BBB\n"
    assert (repo / "c").read_text() == "ccc\n"
    # tree should be the same as before (c's content unchanged)
    assert repo.t(f"git diff --exit-code {sha} HEAD -- c")


def test_edit_abort(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("b", "bbb")
    repo.s("git add .")
    repo.s("git commit -q -m b")

    sha = repo.sha("HEAD")
    branch = repo.head()

    repo.s("git edit :/a; [[ $? = 2 ]]")

    # abort without making changes
    repo.s("git edit --abort")

    assert repo.sha("HEAD") == sha
    assert repo.head() == branch
    assert repo.log() == ["0", "a", "b"]


def test_status_in_progress(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")

    repo.s("git edit HEAD; [[ $? = 2 ]]")
    repo.s("git edit --status")

    # clean up
    repo.s("git edit --abort")


def test_status_no_operation(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.s("git edit --status")


def test_continue_no_operation(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.s("git edit --continue; [[ $? = 1 ]]")


def test_no_commit_arg(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.s("git edit; [[ $? = 1 ]]")


def test_edit_root(repo: Git):
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("b", "bbb")
    repo.s("git add .")
    repo.s("git commit -q -m b")
    assert repo.log() == ["a", "b"]

    repo.s("git edit :/a; [[ $? = 2 ]]")

    repo.w("a", "AAA")
    repo.s("git add .")
    repo.s("git commit --amend -q --no-edit")

    repo.s("git edit --continue")
    assert repo.log() == ["a", "b"]
    assert (repo / "a").read_text() == "AAA\n"
    assert (repo / "b").read_text() == "bbb\n"
    assert all("temp" not in branch for branch in repo.branches())


def test_edit_continue_with_staged_changes(repo: Git):
    """`git edit --continue` with staged changes amends them into the commit."""
    repo.s("git commit --allow-empty -m 0")
    repo.c("a")
    repo.c("c")
    assert repo.log() == ["0", "a", "c"]

    repo.s("git edit :/a; [[ $? = 2 ]]")

    # stage a change, but forget to commit --amend
    repo.w("a", "AAA")
    repo.s("git add .")

    repo.s("git edit --continue")
    assert repo.log() == ["0", "a", "c"]
    assert repo.r("a") == "AAA"
    assert repo.r("c") == "c"
    assert repo.is_clean()


def test_edit_continue_with_unstaged_changes(repo: Git):
    """`git edit --continue` with a dirty tree suspends again, keeping the edits."""
    repo.s("git commit --allow-empty -m 0")
    repo.c("a", content="aaa")
    repo.c("b", filename="a", content="aaa\nbbb")
    assert repo.log() == ["0", "a", "b"]

    repo.s("git edit :/a; [[ $? = 2 ]]")

    # make a change that would conflict with replaying b, and don't stage it
    repo.w("a", "AAA")
    repo.s("git edit --continue; [[ $? = 2 ]]")

    # the user's edit survives, and the operation is still in progress
    assert repo.r("a") == "AAA"
    assert repo.log() == ["0", "a"]
    repo.s("git edit --status")

    # staging it is enough to continue: the edit is amended into commit a,
    # and then replaying b conflicts as it normally would
    repo.s("git add .")
    repo.s("git edit --continue; [[ $? = 2 ]]")
    assert repo.unmerged() == {"a"}
    repo.w("a", "AAA\nbbb")
    repo.s("git add .")
    repo.s("git edit --continue")

    assert repo.log() == ["0", "a", "b"]
    assert repo.r("a") == "AAA\nbbb"
    assert repo.is_clean()


def test_edit_continue_ignores_untracked_files(repo: Git):
    """Untracked files don't block `git edit --continue`."""
    repo.s("git commit --allow-empty -m 0")
    repo.c("a")
    repo.c("c")

    repo.s("git edit :/a; [[ $? = 2 ]]")
    repo.w("junk", "junk")

    repo.s("git edit --continue")
    assert repo.log() == ["0", "a", "c"]
    assert repo.others() == {"junk"}


def test_edit_skip(repo: Git):
    """`git edit --skip` drops a commit that conflicts when it is replayed."""
    repo.s("git commit --allow-empty -m 0")
    repo.c("a", content="aaa")
    repo.c("b", filename="a", content="aaa\nbbb")
    repo.c("c")
    assert repo.log() == ["0", "a", "b", "c"]

    repo.s("git edit :/a; [[ $? = 2 ]]")

    # amend a so that replaying b conflicts
    repo.w("a", "AAA")
    repo.s("git add .")
    repo.s("git commit --amend -q --no-edit")

    repo.s("git edit --continue; [[ $? = 2 ]]")
    assert repo.unmerged() == {"a"}

    # drop b, and the commits above it are still replayed
    repo.s("git edit --skip")
    assert repo.log() == ["0", "a", "c"]
    assert repo.r("a") == "AAA"
    assert repo.r("c") == "c"
    assert repo.is_clean()


def test_edit_skip_no_operation(repo: Git):
    """`git edit --skip` with nothing in progress is an error, not a crash."""
    repo.s("git commit --allow-empty -m 0")
    repo.s("git edit --skip; [[ $? = 1 ]]")


def test_edit_skip_not_allowed_here(repo: Git):
    """`git edit --skip` is refused while the user is editing the commit."""
    repo.s("git commit --allow-empty -m 0")
    repo.c("a")

    repo.s("git edit HEAD; [[ $? = 2 ]]")
    repo.s("git edit --skip", check_error="not allowed here")

    repo.s("git edit --abort")
    assert repo.log() == ["0", "a"]


def conflicting_stack(repo: Git, *, between: bool = False) -> None:
    """
    a, b, [i], c, d where b and c both touch f, so editing b conflicts with c.

    With `between`, i is replayed cleanly before the conflict is reached.
    """
    repo.w("f", "one\ntwo\nthree")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("f", "one\nTWO\nthree")
    repo.s("git add .")
    repo.s("git commit -q -m b")
    if between:
        repo.c("i")
    repo.w("f", "one\nTWO\nTHREE")
    repo.w("g", "ggg")
    repo.s("git add .")
    repo.s("git commit -q -m c")
    repo.w("h", "hhh")
    repo.s("git add .")
    repo.s("git commit -q -m d")


def test_edit_defer(repo: Git):
    conflicting_stack(repo)
    tip = repo.sha("HEAD^{tree}")
    before = {c: repo.so(f"git show --format= :/{c}") for c in "cd"}

    repo.s("git edit :/b; [[ $? = 2 ]]")
    repo.w("f", "one\nBEE\nthree")
    repo.s("git add .")
    repo.s("git edit --continue; [[ $? = 2 ]]")

    repo.s("git edit --defer")

    assert repo.log() == ["a", "b", "📌 defer conflict with c", "c", "d"]

    # the point of the whole exercise: c and d applied unchanged
    for c in "cd":
        assert repo.so(f"git show --format= :/{c}") == before[c]

    # so the deferred commit is the exact inverse of the edit
    assert repo.sha("HEAD^{tree}") == tip
    assert repo.commit(":/defer").message.startswith("📌 defer conflict with c\n")
    assert not repo.so("git status --porcelain")


def test_edit_defer_above_the_first_cherry(repo: Git):
    "the commit that conflicts is not the first one replayed"
    conflicting_stack(repo, between=True)
    assert repo.log() == ["a", "b", "i", "c", "d"]
    before = {c: repo.so(f"git show --format= :/{c}") for c in "cd"}

    repo.s("git edit :/b; [[ $? = 2 ]]")
    repo.w("f", "one\nBEE\nthree")
    repo.s("git add .")
    repo.s("git edit --continue; [[ $? = 2 ]]")

    # i replayed cleanly; c is the one that conflicts
    repo.s("git edit --defer")
    assert repo.log() == ["a", "b", "i", "📌 defer conflict with c", "c", "d"]
    for c in "cd":
        assert repo.so(f"git show --format= :/{c}") == before[c]


def test_edit_defer_is_not_allowed_without_a_conflict(repo: Git):
    repo.c("a")
    repo.c("b")
    repo.s("git edit :/a; [[ $? = 2 ]]")
    repo.s("git edit --defer", check_error="resume is not allowed here")
    repo.s("git edit --abort")


def half_staged_resolution(repo: Git) -> None:
    """
    Reach a conflict, stage a resolution, then keep editing the file.

    That leaves the file changed both in the index and in the working tree,
    which is the state `git cherry-pick --abort` refuses to roll back.
    """
    conflicting_stack(repo)
    repo.s("git edit :/b; [[ $? = 2 ]]")
    repo.w("f", "one\nBEE\nthree")
    repo.s("git add .")
    repo.s("git edit --continue; [[ $? = 2 ]]")
    repo.w("f", "one\nRESOLVED\nthree")
    repo.s("git add .")
    repo.w("f", "one\nSTILL EDITING\nthree")
    assert repo.so("git status --porcelain f") == "MM f"


def test_abort_a_half_staged_resolution(repo: Git):
    half_staged_resolution(repo)
    sha = repo.sha(":/d")

    repo.s("git edit --abort")

    assert repo.sha("HEAD") == sha
    assert not repo.so("git status --porcelain")
    assert not (repo.gitdir / "CHERRY_PICK_HEAD").exists()


def test_skip_a_half_staged_resolution(repo: Git):
    half_staged_resolution(repo)

    repo.s("git edit --skip")

    assert repo.log() == ["a", "b", "d"]
    assert not repo.so("git status --porcelain")
    assert not (repo.gitdir / "CHERRY_PICK_HEAD").exists()


def test_edit_with_untracked_files_in_the_way(repo: Git):
    repo.c("a")
    repo.s("mkdir vendor")
    repo.w("vendor/go.mod", "module x")
    repo.s("git add .")
    repo.s("git commit -q -m vendored")
    target = repo.sha("HEAD")
    repo.s("git rm -q -r --cached vendor && rm -rf vendor")
    repo.s("git commit -q -m unvendored")
    repo.c("c")
    head = repo.sha("HEAD")

    # the file is back in the working tree, but untracked
    repo.s("mkdir vendor")
    repo.w("vendor/go.mod", "module local")

    repo.s(f"git edit {target}", check_error="untracked files would be overwritten")

    # the file is untouched, and so is the branch
    assert repo.sha("HEAD") == head
    assert (repo / "vendor/go.mod").read_text() == "module local\n"
    assert repo.so("git status --porcelain") == "?? vendor/"


def test_edit_below_a_defer_commit(repo: Git):
    "a defer commit pins its tree, so nothing below it can reach past it"
    conflicting_stack(repo)
    # `:/a` would match the defer commit's message once it exists
    root = repo.sha("HEAD~3")
    repo.s("git edit :/b; [[ $? = 2 ]]")
    repo.w("f", "one\nBEE\nthree")
    repo.s("git add .")
    repo.s("git edit --continue; [[ $? = 2 ]]")
    repo.s("git edit --defer")
    tip = repo.sha("HEAD^{tree}")
    assert repo.commit(":/defer").gitq_type == "defer"

    repo.s(f"git edit {root}; [[ $? = 2 ]]")
    repo.w("unrelated", "x")
    repo.s("git add .")
    repo.s("git edit --continue")

    assert repo.log() == ["a", "b", "📌 defer conflict with c", "c", "d"]
    assert repo.sha("HEAD^{tree}") == tip
    assert not (repo / "unrelated").exists()
