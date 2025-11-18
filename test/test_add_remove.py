from .fixtures import Git, repo

_ = repo


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
