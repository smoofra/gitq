from .fixtures import Git, repo

_ = repo


def test_squash(repo: Git):

    for c in "abcde":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")

    repo.s("EDITOR=true git squash HEAD")
    assert "".join(repo.log()) == "abcd"

    repo.s("EDITOR=true git squash :/b")
    assert "".join(repo.log()) == "acd"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert repo.commit("HEAD^^").message.strip() == "a\n\nb"


def test_fixup(repo: Git):

    for c in "abcde":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")

    repo.s("git squash --fixup HEAD")
    assert "".join(repo.log()) == "abcd"

    repo.s("git squash --fixup :/b")
    assert "".join(repo.log()) == "acd"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert repo.commit("HEAD^^").message.strip() == "a"
