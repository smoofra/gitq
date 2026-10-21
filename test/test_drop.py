from .fixtures import Git, repo

_ = repo


def test_drop_head(repo: Git):
    for c in "abcd":
        repo.c(c)

    repo.s("git drop HEAD")
    assert repo.log() == ["a", "b", "c"]


def test_drop_middle(repo: Git):
    for c in "abcde":
        repo.c(c)

    repo.s("git drop :/c")
    assert repo.log() == ["a", "b", "d", "e"]


def test_drop_root(repo: Git):
    for c in "abcde":
        repo.c(c)

    repo.s("git drop :/a")
    assert repo.log() == ["b", "c", "d", "e"]


def test_drop_conflict(repo: Git):
    repo.c("a")
    repo.c("b")
    repo.w("shared", "from_c")
    repo.s("git add shared && git commit -q -m c")
    repo.c("d")
    repo.w("shared", "from_e")
    repo.s("git add shared && git commit -q -m e")

    repo.s("git drop :/c; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    repo.w("shared", "from_e")
    repo.s("git add shared")
    repo.s("git drop --continue")
    assert repo.log() == ["a", "b", "d", "e"]


def test_drop_conflict_resolved_to_empty(repo: Git):
    """A resolution that leaves nothing to commit keeps the commit, empty."""
    repo.c("a")
    repo.w("shared", "from_c")
    repo.s("git add shared && git commit -q -m c")
    repo.w("shared", "from_e")
    repo.s("git add shared && git commit -q -m e")

    repo.s("git drop :/c; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    # resolve by undoing e entirely, so the cherry-pick has nothing left to do
    repo.s("git rm -q --cached shared && rm shared")

    repo.s("git drop --continue")
    assert repo.log() == ["a", "e"]
    assert repo.is_clean()
    assert repo.t("git diff --exit-code HEAD^ HEAD")


def test_drop_conflict_with_unstaged_changes(repo: Git):
    """Unstaged changes suspend the drop again instead of being thrown away."""
    repo.c("a", filename="other", content="other")
    repo.w("shared", "from_c")
    repo.s("git add shared && git commit -q -m c")
    repo.w("shared", "from_e")
    repo.s("git add shared && git commit -q -m e")

    repo.s("git drop :/c; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    repo.w("shared", "resolved")
    repo.s("git add shared")
    repo.w("other", "work in progress")  # forgot to stage this

    repo.s("git drop --continue; [[ $? = 2 ]]")
    assert repo.r("other") == "work in progress"
    assert repo.log() == ["a"]

    repo.s("git add other")
    repo.s("git drop --continue")
    assert repo.log() == ["a", "e"]
    assert repo.r("other") == "work in progress"
    assert repo.r("shared") == "resolved"
    assert repo.is_clean()
