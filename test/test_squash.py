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


def test_squash_deleted(repo: Git):

    repo.s("echo a >a && git add a && git commit -m a")
    repo.s("echo b >b && git add b && git commit -m b")
    repo.s("echo c >c && git add c && git commit -m c")
    repo.s("git rm b && git commit -m B")
    repo.s("echo d >d && git add d && git commit -m d")

    assert "".join(repo.log()) == "abcBd"
    sha = repo.rev_parse("HEAD")

    repo.s("EDITOR=true git squash :/B")
    assert "".join(repo.log()) == "abcd"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert not repo.others()

    repo.s("EDITOR=true git squash HEAD")
    assert "".join(repo.log()) == "abc"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert not repo.others()


def test_fixup_deleted(repo: Git):

    repo.s("echo a >a && git add a && git commit -m a")
    repo.s("echo b >b && git add b && git commit -m b")
    repo.s("echo c >c && git add c && git commit -m c")
    repo.s("git rm b && git commit -m B")
    repo.s("echo d >d && git add d && git commit -m d")

    assert "".join(repo.log()) == "abcBd"
    sha = repo.rev_parse("HEAD")

    repo.s("git squash --fixup :/B")
    assert "".join(repo.log()) == "abcd"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert not repo.others()

    repo.s("git squash --fixup HEAD")
    assert "".join(repo.log()) == "abc"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert not repo.others()
