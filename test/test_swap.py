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
        subprocess.run(command, shell=True, check=True, cwd=self.path)

    def t(self, command: str) -> bool:
        "run a shell command and return success or failure"
        return subprocess.run(command, shell=True, cwd=self.path).returncode == 0

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
    assert repo.log() == ["init", "a", "x"]
    sha = repo.rev_parse("HEAD")
    repo.s("git swap")
    assert repo.t(f"git diff {sha} HEAD")
    assert repo.log() == ["init", "x", "a"]
