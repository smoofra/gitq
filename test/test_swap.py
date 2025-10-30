from .fixtures import Git, repo

_ = repo


def test_swap(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("x", "xxx")
    repo.s("git add .")
    repo.s("git commit -q -m x")
    assert repo.log() == ["0", "a", "x"]
    sha = repo.rev_parse("HEAD")
    repo.s("git swap")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["0", "x", "a"]


def test_swap_root(repo: Git):
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("x", "xxx")
    repo.s("git add .")
    repo.s("git commit -q -m x")
    sha = repo.rev_parse("HEAD")
    assert repo.log() == ["a", "x"]
    repo.s("git swap")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["x", "a"]
    assert all("temp" not in branch for branch in repo.branches())


def test_swap_empty(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "a0"
    repo.s("git swap")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "0a"


def test_resume(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("a", "aaa\nbbb")
    repo.s("git add .")
    repo.s("git commit -q -m b")
    assert repo.log() == ["0", "a", "b"]
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap --edit")
    repo.w("a", "bbb")
    repo.s("git add -u")
    repo.s("git swap --continue")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["0", "b", "a"]


def test_resume_root(repo: Git):
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("a", "aaa\nbbb")
    repo.s("git add .")
    repo.s("git commit -q -m b")
    assert repo.log() == ["a", "b"]
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap --edit")
    repo.w("a", "bbb")
    repo.s("git add -u")
    repo.s("git swap --continue")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["b", "a"]


def test_keep_going(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    repo.w("b", "BBBBBB")
    repo.s("git add .")
    repo.s("git commit -q -m B")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abBcdefg"


def test_keep_going_baseline(repo: Git):
    for c in "abcde":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
        if c == "b":
            repo.s("git queue init HEAD")
    repo.w("x", "x")
    repo.s("git add .")
    repo.s("git commit -q -m X")
    sha = repo.rev_parse("HEAD")
    assert repo.log() == ["a", "b", "initialized queue", "c", "d", "e", "X"]
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["a", "b", "initialized queue", "X", "c", "d", "e"]


def test_middle(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap :/e")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abcedfg"


def test_middle_keep_going(repo: Git):

    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    repo.w("b", "BBBBBB")
    repo.s("git add .")
    repo.s("git commit -q -m B")

    for c in "hij":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going :/B")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abBcdefghij"


def test_keep_going_root(repo: Git):
    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")

    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m b")

    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "ba"


def test_keep_going_root_longer(repo: Git):
    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "dabc"


def test_keep_going_root_longer_empty(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "d0abc"


def test_resume_middle(repo: Git):

    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    repo.w(
        "d",
        """
        d
        D
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m D")

    for c in "efg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    sha = repo.rev_parse("HEAD")
    repo.s("! git swap -e :/D")

    repo.w("d", "D")
    repo.s("git add -u")
    repo.s("git swap --continue")

    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abcDdefg"


def test_resume_middle_fixup(repo: Git):

    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    repo.w(
        "d",
        """
        d
        D
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m D")

    for c in "efg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    sha = repo.rev_parse("HEAD")
    repo.s("! git swap -e :/D")
    repo.s("git swap --fixup")

    assert repo.t(f"git diff --exit-code --quiet {sha} HEAD")
    assert "".join(repo.log()) == "abcdefg"


def test_resume_fixup(repo: Git):

    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")

    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m b")

    repo.w("b", "B")
    repo.s("git add .")
    repo.s("git commit -q -m B")

    repo.w("c", "c")
    repo.s("git add .")
    repo.s("git commit -q -m c")

    assert "".join(repo.log()) == "abBc"
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap -e :/B")
    repo.s("git swap --fixup")
    assert repo.t(f"git diff --exit-code --quiet {sha} HEAD")
    assert "".join(repo.log()) == "abc"


def test_resume_squash(repo: Git):

    repo.s("git config author.name Foo")
    repo.s("git config author.email foo@example.com")

    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")

    repo.s("git config author.name Bar")
    repo.s("git config author.email bar@example.com")

    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m b")

    author = repo.commit("HEAD").author

    repo.s("git config author.name Foo")
    repo.s("git config author.email foo@example.com")

    repo.w("b", "B")
    repo.s("git add .")
    repo.s("git commit -q -m B")

    repo.w("c", "c")
    repo.s("git add .")
    repo.s("git commit -q -m c")

    assert "".join(repo.log()) == "abBc"
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap -e :/B")
    repo.s("EDITOR=true git swap --squash")
    assert repo.t(f"git diff --exit-code --quiet {sha} HEAD")
    assert "".join(repo.log()) == "abc"

    a = repo.commit("HEAD^^")
    assert a.message == "a\n"
    assert a.author.startswith("Foo <foo@example.com>")

    b = repo.commit("HEAD^")
    assert b.message == "b\n\nB\n"
    assert b.author == author
    assert b.author.startswith("Bar <bar@example.com>")

    c = repo.commit("HEAD")
    assert c.message == "c\n"
    assert c.author.startswith("Foo <foo@example.com>")


def test_resume_fixup_root(repo: Git):

    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")

    repo.w("a", "A")
    repo.s("git add .")
    repo.s("git commit -q -m A")

    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m b")

    repo.w("c", "c")
    repo.s("git add .")
    repo.s("git commit -q -m c")

    assert "".join(repo.log()) == "aAbc"
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap -e :/A")
    repo.s("git swap --fixup")
    assert repo.t(f"git diff --exit-code --quiet {sha} HEAD")
    assert "".join(repo.log()) == "abc"


def test_resume_squash_root(repo: Git):

    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")

    repo.w("a", "A")
    repo.s("git add .")
    repo.s("git commit -q -m A")

    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m b")

    repo.w("c", "c")
    repo.s("git add .")
    repo.s("git commit -q -m c")

    assert "".join(repo.log()) == "aAbc"
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap -e :/A")
    repo.s("EDITOR=true git swap --squash")
    assert repo.t(f"git diff --exit-code --quiet {sha} HEAD")
    assert "".join(repo.log()) == "abc"

    a = repo.commit("HEAD^^")
    assert a.message == "a\n\nA\n"


def test_keep_going_stop(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    repo.w("b", "BBBBBB")
    repo.s("git add .")
    repo.s("git commit -q -m B")
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap --keep-going -e")
    repo.s("git swap --stop")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abBcdefg"


def test_keep_going_fixup(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    repo.w("b", "BBBBBB")
    repo.s("git add .")
    repo.s("git commit -q -m B")
    sha = repo.rev_parse("HEAD")
    assert "".join(repo.log()) == "abcdefgB"
    repo.s("! git swap --keep-going -e")
    repo.s("git swap --fixup")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abcdefg"


def test_keep_going_squash(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    repo.w("b", "BBBBBB")
    repo.s("git add .")
    repo.s("git commit -q -m B")
    sha = repo.rev_parse("HEAD")
    assert "".join(repo.log()) == "abcdefgB"
    repo.s("! git swap --keep-going -e")
    repo.s("EDITOR=true git swap --squash")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abcdefg"


def test_keep_going_continue(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    repo.w("c", "c\nC")
    repo.s("git add .")
    repo.s("git commit -q -m C")
    sha = repo.rev_parse("HEAD")
    assert "".join(repo.log()) == "abcdefgC"
    repo.s("! git swap --keep-going -e")
    repo.w("c", "C")
    repo.s("git add -u")
    repo.s("git swap --continue")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "Cabcdefg"


def test_swap_failed_root(repo: Git):
    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("a", "A")
    repo.s("git add .")
    repo.s("git commit -q -m A")
    repo.s("! git swap")
    assert "".join(repo.log()) == "aA"


def test_resume_twice(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("a", "a\nb")
    repo.s("git add .")
    repo.s("git commit -q -m b")
    assert repo.log() == ["0", "a", "b"]
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap --edit")
    repo.s("! git swap --continue")
    repo.w("a", "b")
    repo.s("git add -u")
    repo.s("git swap --continue")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["0", "b", "a"]


def test_keep_going_resume_twice(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    repo.w("z", "z")
    repo.w("a", "a\nz")
    repo.w("d", "d\nz")
    repo.s("git add .")
    repo.s("git commit -q -m z")

    sha = repo.rev_parse("HEAD")
    repo.s("! git swap --keep-going --edit")

    assert repo.unmerged_files() == {"d"}
    repo.w("d", "z")
    repo.s("git add d")

    repo.s("! git swap --continue")

    assert repo.unmerged_files() == {"a"}
    repo.w("a", "z")
    repo.s("git add a")

    repo.s("git swap --continue")

    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "zabcdefg"


def test_swap_up(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("x", "xxx")
    repo.s("git add .")
    repo.s("git commit -q -m x")
    assert repo.log() == ["0", "a", "x"]
    sha = repo.rev_parse("HEAD")
    repo.s("git swap --up HEAD^")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["0", "x", "a"]


def test_swap_up_root(repo: Git):
    repo.w("a", "aaa")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w("x", "xxx")
    repo.s("git add .")
    repo.s("git commit -q -m x")
    sha = repo.rev_parse("HEAD")
    assert repo.log() == ["a", "x"]
    repo.s("git swap --up HEAD^")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.log() == ["x", "a"]
    assert all("temp" not in branch for branch in repo.branches())


def test_keep_going_up(repo: Git):
    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    repo.w("a", "A")
    repo.s("git add .")
    repo.s("git commit -q -m A")

    for c in "efg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    assert "".join(repo.log()) == "abcdAefg"
    sha = repo.rev_parse("HEAD")

    repo.s("git swap --keep-going --up :/a")

    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "bcdaAefg"


def test_keep_going_up_fixup(repo: Git):
    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    repo.w("a", "A")
    repo.s("git add .")
    repo.s("git commit -q -m A")

    for c in "efg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    assert "".join(repo.log()) == "abcdAefg"
    sha = repo.rev_parse("HEAD")

    repo.s("! git swap --keep-going --up --edit :/a")

    repo.s("git swap --fixup")

    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "bcdaefg"


def test_middle_up(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap --up :/d")
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abcedfg"


def test_resume_middle_up(repo: Git):

    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    repo.w(
        "d",
        """
        d
        D
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m D")

    for c in "efg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")

    assert "".join(repo.log()) == "abcdDefg"
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap -e --up :/d")

    repo.w("d", "D")
    repo.s("git add -u")
    repo.s("git swap --continue")

    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert "".join(repo.log()) == "abcDdefg"
