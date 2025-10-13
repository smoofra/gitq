import sys
import subprocess
import textwrap
from pathlib import Path
from typing import List
import os
import shutil

import pytest

import git_quilt.git


class Directory:

    def __init__(self, path: Path):
        self.path = path

    def __truediv__(self, rel: str) -> Path:
        return self.path / rel

    def s(self, command: str):
        "run a shell command"
        sys.stdout.flush()
        sys.stderr.flush()
        subprocess.run(command, shell=True, check=True, cwd=self.path, stderr=sys.stdout)

    def t(self, command: str) -> bool:
        "run a shell command and return success or failure"
        sys.stdout.flush()
        sys.stderr.flush()
        proc = subprocess.run(command, shell=True, cwd=self.path, stderr=sys.stdout)
        return proc.returncode == 0

    def w(self, filename: str, content: str):
        "write a file"
        with open(self / filename, "w") as f:
            f.write(textwrap.dedent(content).strip())
            f.write("\n")


class Git(Directory, git_quilt.git.Git):

    def __init__(self, path: Path):
        Directory.__init__(self, path)
        if not (path / ".git").exists():
            self.s("git init -q")
        git_quilt.git.Git.__init__(self, path)

    def log(self, n=None) -> List[str]:
        command = ["git", "log", "--reverse", "--pretty=format:%s"]
        if n is not None:
            command.append(f"-n{n}")
        return [line.strip() for line in self.cmd(command, quiet=True).splitlines()]


@pytest.fixture(scope="function")
def repo(tmp_path: Path) -> Git:
    if "GIT_QUILT_TEMP_REPO" in os.environ:
        tmp_path = Path(os.environ["GIT_QUILT_TEMP_REPO"])

    os.makedirs(tmp_path, exist_ok=True)

    for x in tmp_path.glob("*"):
        if x.is_dir():
            shutil.rmtree(x)
        else:
            x.unlink()

    repo = Git(tmp_path)
    repo.s("git commit --allow-empty -m 0")
    return repo


def test_swap(repo: Git):
    repo.w(
        "a",
        """
            aaa
            bbb
            ccc
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w(
        "x",
        """
            xxx
            yyy
            zzz
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m x")
    assert repo.log() == ["0", "a", "x"]
    sha = repo.rev_parse("HEAD")
    repo.s("git swap")
    assert repo.t(f"git diff {sha} HEAD")
    assert repo.log() == ["0", "x", "a"]


def test_swap_root(repo: Git):
    repo.w(
        "a",
        """
            aaa
            bbb
            ccc
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q --amend -m a")
    repo.w(
        "x",
        """
            xxx
            yyy
            zzz
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m x")
    sha = repo.rev_parse("HEAD")
    assert repo.log() == ["a", "x"]
    repo.s("git swap")
    assert repo.t(f"git diff {sha} HEAD")
    assert repo.log() == ["x", "a"]
    assert all("temp" not in branch for branch in repo.branches())


def test_swap_empty(repo: Git):
    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q -m a")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap")
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "a0"
    repo.s("git swap")
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "0a"


def test_resume(repo: Git):

    repo.w(
        "a",
        """
            aaa
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m a")
    repo.w(
        "a",
        """
            aaa
            bbb
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m b")
    assert repo.log() == ["0", "a", "b"]
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap --edit")
    repo.w(
        "a",
        """
            bbb
        """,
    )
    repo.s("git add -u")
    repo.s("git swap --continue")
    assert repo.t(f"git diff {sha} HEAD")
    assert repo.log() == ["0", "b", "a"]


def test_resume_root(repo: Git):

    repo.w(
        "a",
        """
            aaa
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q --amend -m a")
    repo.w(
        "a",
        """
            aaa
            bbb
        """,
    )
    repo.s("git add .")
    repo.s("git commit -q -m b")
    assert repo.log() == ["a", "b"]
    sha = repo.rev_parse("HEAD")
    repo.s("! git swap --edit")
    repo.w(
        "a",
        """
            bbb
        """,
    )
    repo.s("git add -u")
    repo.s("git swap --continue")
    assert repo.t(f"git diff {sha} HEAD")
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
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "0abBcdefg"


def test_middle(repo: Git):
    for c in "abcdefg":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap :/e")
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "0abcedfg"


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
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "0abBcdefghij"


def test_keep_going_root(repo: Git):
    repo.w("a", "a")
    repo.s("git add .")
    repo.s("git commit -q --amend -m a")

    repo.w("b", "b")
    repo.s("git add .")
    repo.s("git commit -q -m b")

    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "ba"


def test_keep_going_root_longer(repo: Git):
    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        if c == "a":
            repo.s("git commit -q --amend -m a")
        else:
            repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "dabc"


def test_keep_going_root_longer_empty(repo: Git):
    for c in "abcd":
        repo.w(c, c)
        repo.s("git add .")
        repo.s(f"git commit -q -m {c}")
    sha = repo.rev_parse("HEAD")
    repo.s("git swap --keep-going")
    assert repo.t(f"git diff {sha} HEAD")
    assert "".join(repo.log()) == "d0abc"
