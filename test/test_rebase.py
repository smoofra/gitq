from .fixtures import Git, repo

_ = repo


def test_rebase(repo: Git):
    repo.w("a", "a")
    repo.s("git add a && git commit -m a")
    repo.s("git branch base HEAD")
    base0 = repo.rev_parse("HEAD")

    repo.w("b", "b")
    repo.s("git add b && git commit -m b")

    repo.s("git quilt init base")

    repo.s("git checkout base")
    repo.w("a", "A")
    repo.s("git commit -a --amend -m A")
    base1 = repo.rev_parse("HEAD")

    repo.s("git checkout master")
    assert repo.log() == ["a", "b", "initialized quilt"]
    assert [b.sha for b in repo.q.baselines] == [base0]

    repo.s("git quilt rebase")
    assert repo.log() == ["A", "baseline", "b"]
    assert [b.sha for b in repo.q.baselines] == [base1]


def test_two_baselines(repo: Git):

    repo.s("git commit --allow-empty -m0")

    repo.s("git checkout -b a master")
    repo.w("a", "a")
    repo.s("git add a && git commit -m a")
    a = repo.rev_parse("HEAD")

    repo.s("git checkout -b b master")
    repo.w("b", "b")
    repo.s("git add b && git commit -m b")
    b = repo.rev_parse("HEAD")

    repo.s("git checkout -b c master")
    repo.s("git quilt init a b")
    repo.w("c", "c")
    repo.s("git add c && git commit -m c")
    assert [b.sha for b in repo.q.baselines] == [a, b]

    repo.s("git quilt rebase")
    assert repo.log() == ["0", "a", "b", "merged baselines", "c"]

    repo.s("git checkout a")
    repo.w("a", "A")
    repo.s("git commit -a  -m A")
    A = repo.rev_parse("HEAD")

    repo.s("git checkout c")
    repo.s("git quilt rebase")
    assert repo.log() == ["0", "a", "A", "b", "merged baselines", "c"]
    assert [b.sha for b in repo.q.baselines] == [A, b]
