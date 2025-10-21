import sys
import subprocess
import textwrap
from pathlib import Path
from typing import List
import os
import shutil

import pytest

import gitq.git
from gitq.git_queue import Queue

__all__ = ["Git", "repo"]


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


class Git(Directory, gitq.git.Git):

    def __init__(self, path: Path):
        Directory.__init__(self, path)
        if not (path / ".git").exists():
            self.s("git init -q")
        gitq.git.Git.__init__(self, path)

    def log(self, n=None) -> List[str]:
        command = ["git", "log", "--topo-order", "--reverse", "--format=%s"]
        if n is not None:
            command.append(f"-n{n}")
        return [line.strip() for line in self.cmd(command, quiet=True).splitlines()]

    @property
    def q(self):
        return Queue(self).q


@pytest.fixture(scope="function")
def repo(tmp_path: Path) -> Git:
    if "GIT_QUEUE_TEMP_REPO" in os.environ:
        tmp_path = Path(os.environ["GIT_QUEUE_TEMP_REPO"])

    os.makedirs(tmp_path, exist_ok=True)

    for x in tmp_path.glob("*"):
        if x.is_dir():
            shutil.rmtree(x)
        else:
            x.unlink()

    repo = Git(tmp_path)
    return repo
