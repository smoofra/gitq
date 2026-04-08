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

    repo.s("git drop --edit :/c; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    repo.w("shared", "from_e")
    repo.s("git add shared")
    repo.s("git drop --continue")
    assert repo.log() == ["a", "b", "d", "e"]
