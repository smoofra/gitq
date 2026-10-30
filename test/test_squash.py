from .fixtures import Git, repo

_ = repo


def test_squash(repo: Git):

    for c in "abcde":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.sha("HEAD")

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
    sha = repo.sha("HEAD")

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
    sha = repo.sha("HEAD")

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
    sha = repo.sha("HEAD")

    repo.s("git squash --fixup :/B")
    assert "".join(repo.log()) == "abcd"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert not repo.others()

    repo.s("git squash --fixup HEAD")
    assert "".join(repo.log()) == "abc"
    assert repo.t(f"git diff --quiet {sha} HEAD")
    assert not repo.others()


def abc(repo: Git) -> None:
    for c in "abc":
        repo.c(c)


def test_squash_no_edit(repo: Git):
    abc(repo)
    repo.s("EDITOR=false git squash --no-edit :/c")
    assert repo.commit("HEAD").message == "b\n\nc\n"


def test_squash_no_edit_message(repo: Git):
    abc(repo)
    repo.s("EDITOR=false git squash --no-edit-message :/c")
    assert repo.commit("HEAD").message == "b\n\nc\n"


def test_squash_message(repo: Git):
    abc(repo)
    repo.s("EDITOR=false git squash -m 'squashed by hand' :/c")
    assert repo.commit("HEAD").message == "squashed by hand\n"


def test_squash_message_from_a_file(repo: Git):
    abc(repo)
    repo.w("msg.txt", "from a file\n\nwith a body")
    repo.s("EDITOR=false git squash -F msg.txt :/c")
    assert repo.commit("HEAD").message == "from a file\n\nwith a body\n"


def test_squash_message_not_with_fixup(repo: Git):
    abc(repo)
    # argparse errors go to stderr
    repo.s("git squash -f -m x :/c 2>&1", check_error="not used with --fixup")


def test_squash_up(repo: Git):
    for c in "abcd":
        repo.c(c)
    sha = repo.sha("HEAD")

    # b and c become one commit, carrying b's message first
    repo.s("EDITOR=false git squash --up --no-edit :/b")
    assert "".join(repo.log()) == "abd"
    assert repo.commit("HEAD^").message == "b\n\nc\n"
    assert repo.t(f"git diff --quiet {sha} HEAD")


def two_authors(repo: Git) -> None:
    repo.s("git -c user.name=Bottom -c user.email=b@e commit -q --allow-empty -m bottom")
    repo.s("git -c user.name=Top -c user.email=t@e commit -q --allow-empty -m top")


def test_squash_up_keeps_the_later_author(repo: Git):
    "--up squashes into the later commit, so that is the one that survives"
    two_authors(repo)
    repo.s("EDITOR=false git squash --up --no-edit :/bottom")
    assert repo.log() == ["bottom"]
    assert repo.commit("HEAD").author.startswith("Top <t@e>")
    assert repo.commit("HEAD").message == "bottom\n\ntop\n"


def test_squash_down_keeps_the_earlier_author(repo: Git):
    "...whereas squashing the same pair downwards keeps the earlier one"
    two_authors(repo)
    repo.s("EDITOR=false git squash --no-edit :/top")
    assert repo.log() == ["bottom"]
    assert repo.commit("HEAD").author.startswith("Bottom <b@e>")
    assert repo.commit("HEAD").message == "bottom\n\ntop\n"


def test_squash_up_fixup_keeps_the_later_message(repo: Git):
    two_authors(repo)
    repo.s("git squash --up --fixup :/bottom")
    assert repo.log() == ["top"]
    assert repo.commit("HEAD").author.startswith("Top <t@e>")
    assert repo.commit("HEAD").message == "top\n"


def test_squash_up_at_head(repo: Git):
    for c in "ab":
        repo.c(c)
    repo.s("git squash --up HEAD", check_error="already at HEAD")


def test_squash_up_fixup(repo: Git):
    for c in "abc":
        repo.c(c)
    # b is squashed into c, so c's message is the one kept
    repo.s("git squash --up --fixup :/b")
    assert "".join(repo.log()) == "ac"
    assert repo.commit("HEAD").message == "c\n"
