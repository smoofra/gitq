import sys
import subprocess
import textwrap
from pathlib import Path
from typing import List
import os
import shutil
import re
from contextlib import contextmanager

import pytest

from gitq.output import Output
from gitq.git import Git as BaseGit
from gitq.git_queue import Queue

__all__ = ["Git", "repo"]


class Directory:

    path: Path

    def __init__(self, path: Path):
        self.path = path

    def __truediv__(self, rel: str) -> Path:
        return self.path / rel

    def s(self, command: str, *, check_error: str = ""):
        "run a shell command"
        Output.log_cmd(command)
        Output.flush()
        c = ["bash", "-c", command]
        with Output.indent():
            if check_error:
                p = subprocess.run(c, cwd=self.path, text=True, capture_output=True)
                assert p.returncode != 0
                assert re.search(check_error, p.stdout)
            else:
                subprocess.run(c, check=True, cwd=self.path, stderr=sys.stdout)

    def t(self, command: str) -> bool:
        "run a shell command and return success or failure"
        Output.flush()
        with Output.indent():
            proc = subprocess.run(["bash", "-c", command], cwd=self.path, stderr=sys.stdout)
            return proc.returncode == 0

    def w(self, filename: str, content: str):
        "write a file"
        with open(self / filename, "w") as f:
            f.write(textwrap.dedent(content).strip())
            f.write("\n")


class Git(Directory, BaseGit):

    def __init__(self, path: Path):
        Directory.__init__(self, path)
        if not (path / ".git").exists():
            self.s("git init -q")
            self.s("git config set advice.detachedHead false")
        BaseGit.__init__(self, path)

    def log(self, n=None) -> List[str]:
        command = ["git", "log", "--topo-order", "--reverse", "--format=%s"]
        if n is not None:
            command.append(f"-n{n}")
        return [line.strip() for line in self.cmd(command, quiet=True).splitlines()]

    def unmerged(self) -> set[str]:
        lines = self("ls-files", "-u").splitlines()
        return {line.split("\t", 1)[1] for line in lines}

    def others(self) -> set[str]:
        return set(self("ls-files", "--others").splitlines())

    def print_graph(self, *args: str) -> None:
        graph = self.cmd(
            ["git", "log", "-n20", "--graph", "--decorate", "--oneline", "--left-right", *args],
            quiet=True,
        )
        n = max(len(line.rstrip()) for line in graph.splitlines())
        Output.print("=" * n)
        Output.print(graph.strip())
        Output.print("=" * n)

    @property
    def q(self):
        return Queue(self).qf

    def c(self, message: str, *, filename: str | None = None, content: str | None = None):
        if filename is None:
            filename = message
        if content is None:
            content = message
        self.w(filename, content)
        self("add", filename)
        self("commit", "--allow-empty", "-q", "-m", message)


@pytest.fixture(scope="function")
def repo(tmp_path: Path) -> Git:
    Output.print()

    if t := os.environ.get("GIT_QUEUE_TEMP_REPO"):
        if os.environ.get("PYTEST_XDIST_WORKER"):
            raise RuntimeError("GIT_QUEUE_TEMP_REPO cannot be used with parallel tests")
        tmp_path = Path(t)

    os.makedirs(tmp_path, exist_ok=True)

    for x in tmp_path.glob("*"):
        if x.is_dir():
            shutil.rmtree(x)
        else:
            x.unlink()

    repo = Git(tmp_path)
    return repo


@contextmanager
def env(**kw):
    old: dict[str, str | None] = dict()
    for key, value in kw.items():
        old[key] = os.environ.get(key, None)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                del os.environ[key]
            else:
                os.environ[key] = value
