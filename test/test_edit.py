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
