from .fixtures import Git, repo

_ = repo


def test_rebase(repo: Git):
    repo.w("a", "a")
    repo.s("git add a && git commit -m a")
    repo.s("git branch base HEAD")
    base0 = repo.rev_parse("HEAD")

    repo.w("b", "b")
    repo.s("git add b && git commit -m b")

    repo.s("git queue init base")

    repo.s("git checkout base")
    repo.w("a", "A")
    repo.s("git commit -a --amend -m A")
    base1 = repo.rev_parse("HEAD")

    repo.s("git checkout master")
    assert repo.log() == ["a", "b", "initialized queue"]
    assert [b.sha for b in repo.q.baselines] == [base0]

    repo.s("git queue rebase")
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
    repo.s("git queue init a b")
    repo.w("c", "c")
    repo.s("git add c && git commit -m c")
    assert [b.sha for b in repo.q.baselines] == [a, b]

    repo.s("git queue rebase")
    assert repo.log() == ["0", "a", "b", "merged baselines", "c"]

    repo.s("git checkout a")
    repo.w("a", "A")
    repo.s("git commit -a  -m A")
    A = repo.rev_parse("HEAD")

    repo.s("git checkout c")
    repo.s("git queue rebase")
    assert repo.log() == ["0", "a", "A", "b", "merged baselines", "c"]
    assert [b.sha for b in repo.q.baselines] == [A, b]


def test_rebase_merge(repo: Git):
    repo.w("a", "a")
    repo.s("git add a && git commit -m a")
    repo.s("git branch base HEAD")

    repo.s("git queue init base")
    q0 = repo.rev_parse("HEAD")

    repo.w("b", "b")
    repo.s("git add b && git commit -m b")

    repo.s(f"git checkout {q0}")
    repo.w("c", "c")
    repo.s("git add c && git commit -m c")

    repo.s("git checkout master")
    repo.s("git merge HEAD@{1}")

    repo.s("git checkout base")
    repo.w("a", "A")
    repo.s("git commit -a --amend -m A")
    base1 = repo.rev_parse("HEAD")

    repo.s("git checkout master")
    repo.s("git queue rebase")

    assert repo.log() == ["A", "baseline", "b", "c"]
    assert [b.sha for b in repo.q.baselines] == [base1]


def test_rebase_conflict(repo: Git):
    repo.w("a", "a")
    repo.s("git add a && git commit -q -m a")
    repo.s("git branch base HEAD")

    repo.w("b", "b")
    repo.s("git add b && git commit -q -m b")

    repo.w("c", "c")
    repo.s("git add c && git commit -q -m c")

    repo.s("git queue init base")

    repo.s("git checkout base")
    repo.w("a", "A")
    repo.w("b", "")
    repo.w("c", "")
    repo.s("git add a b c")
    repo.s("git commit -a --amend -m A -q")
    base1 = repo.rev_parse("HEAD")

    repo.s("git checkout master")
    sha = repo.rev_parse("HEAD")

    repo.s("! git queue rebase")
    assert repo.unmerged() == {"b"}

    repo.w("b", "b")
    repo.s("git add b")
    repo.s("! git queue continue")

    assert repo.unmerged() == {"c"}
    repo.w("c", "c")
    repo.s("git add c")
    repo.s("git queue continue")

    assert repo.log() == ["A", "baseline", "b", "c"]
    assert [b.sha for b in repo.q.baselines] == [base1]
    assert set(repo("diff", "--name-only", sha, "HEAD").splitlines()) == {"a", ".git-queue"}
