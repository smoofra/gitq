from .fixtures import Git, repo

_ = repo


def test_split(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "a")
    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m ab")
    sha = repo.rev_parse("HEAD")
    assert repo.log() == ["0", "ab"]

    repo.s("git split HEAD; [[ $? = 2 ]]")

    # check status message contains ref to the split target
    repo.s("git split --status | grep " + repo.abbrev(sha))

    # changes from 'ab' are staged; unstage b and commit just a
    repo.s("git restore --staged b")
    repo.s("git commit -q -m a")

    # first continue: commits remaining changes with original message, suspends for amend
    repo.s("git split --continue; [[ $? = 2 ]]")

    # check using wrong tool to get status
    repo.s("git queue status | grep " + repo.abbrev(sha))

    # second continue: done
    repo.s("git split --continue")

    assert repo.log() == ["0", "a", "ab"]
    assert repo.t(f"git diff --exit-code {sha} HEAD")


def test_split_amend_message(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "a")
    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m ab")
    sha = repo.rev_parse("HEAD")

    repo.s("git split HEAD; [[ $? = 2 ]]")
    repo.s("git restore --staged b")
    repo.s("git commit -q -m a")

    # first continue: suspends for amend
    repo.s("git split --continue; [[ $? = 2 ]]")

    # amend the commit message before continuing
    repo.s("git commit --amend -m b")
    repo.s("git split --continue")

    assert repo.log() == ["0", "a", "b"]
    assert repo.t(f"git diff --exit-code {sha} HEAD")
    assert repo.commit("HEAD").message == "b\n"


def test_split_middle(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "a")
    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m ab")
    repo.w("c", "c")
    repo.s("git add .")
    repo.s("git commit -q -m c")
    sha = repo.rev_parse("HEAD")
    assert repo.log() == ["0", "ab", "c"]

    repo.s("git split :/ab; [[ $? = 2 ]]")
    repo.s("git restore --staged b")
    repo.s("git commit -q -m a")
    repo.s("git split --continue; [[ $? = 2 ]]")
    repo.s("git split --continue")

    assert repo.log() == ["0", "a", "ab", "c"]
    assert repo.t(f"git diff --exit-code {sha} HEAD")


def test_split_abort(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "a")
    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m ab")
    sha = repo.rev_parse("HEAD")

    repo.s("git split HEAD; [[ $? = 2 ]]")
    repo.s("git split --abort")

    assert repo.log() == ["0", "ab"]
    assert repo.t(f"git diff --exit-code {sha} HEAD")


def test_split_abort_after_first_continue(repo: Git):
    repo.s("git commit --allow-empty -m 0")
    repo.w("a", "a")
    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m ab")
    sha = repo.rev_parse("HEAD")

    repo.s("git split HEAD; [[ $? = 2 ]]")
    repo.s("git restore --staged b")
    repo.s("git commit -q -m a")
    repo.s("git split --continue; [[ $? = 2 ]]")
    repo.s("git split --abort")

    assert repo.log() == ["0", "ab"]
    assert repo.t(f"git diff --exit-code {sha} HEAD")
