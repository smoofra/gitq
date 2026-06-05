import sys
import subprocess
import textwrap
from pathlib import Path
from typing import List, Iterator
import os
import shutil
import re
from tempfile import TemporaryDirectory
from contextlib import contextmanager

import pytest

from gitq.output import Output
from gitq.git import Git as BaseGit
from gitq.git_queue import Queue, DETECT


__all__ = ["Git", "repo", "remote_repo", "tmp"]


class Git(BaseGit):

    def __truediv__(self, rel: str) -> Path:
        return self.directory / rel

    def s(self, command: str, *, check_error: str = ""):
        "run a shell command"
        Output.log_cmd(command)
        Output.flush()
        c = ["bash", "-c", command]
        with Output.indent():
            if check_error:
                p = subprocess.run(
                    c, cwd=self.directory, text=True, capture_output=True, env=self.env
                )
                assert p.returncode != 0
                assert re.search(check_error, p.stdout)
            else:
                subprocess.run(c, check=True, cwd=self.directory, stderr=sys.stdout, env=self.env)

    def so(self, command: str) -> str:
        "run a shell command and return output"
        Output.flush()
        c = ["bash", "-c", command]
        p = subprocess.run(
            c, check=True, text=True, cwd=self.directory, capture_output=True, env=self.env
        )
        return p.stdout.strip()

    def t(self, command: str) -> bool:
        "run a shell command and return success or failure"
        Output.flush()
        with Output.indent():
            proc = subprocess.run(
                ["bash", "-c", command], cwd=self.directory, stderr=sys.stdout, env=self.env
            )
            return proc.returncode == 0

    def w(self, filename: str, content: str):
        "write a file"
        with open(self.directory / filename, "w") as f:
            f.write(textwrap.dedent(content).strip())
            f.write("\n")

    def r(self, filename: str) -> str:
        "read a file"
        with open(self.directory / filename, "r") as f:
            return f.read().strip()

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
    def qf(self):
        return Queue(self, bare=DETECT).qf

    def c(self, message: str, *, filename: str | None = None, content: str | None = None):
        if filename is None:
            filename = message
        if content is None:
            content = message
        self.w(filename, content)
        self("add", filename)
        self("commit", "--allow-empty", "-q", "-m", message)


@contextmanager
def tmp() -> Iterator[Path]:

    if (t := os.environ.get("GITQ_TEMP")) and not os.environ.get("PYTEST_XDIST_WORKER"):
        path = Path(t) / "temp"
        assert path.is_absolute()
        os.makedirs(path, exist_ok=True)
        for x in path.glob("*"):
            if x.is_dir():
                shutil.rmtree(x)
            else:
                x.unlink()
        yield path

    else:
        with TemporaryDirectory() as t:
            yield Path(t)


@pytest.fixture(scope="function")
def remote_repo() -> Iterator[tuple[Git, Git]]:
    """Returns (local, remote) git pair. local has 'origin' remote pointing to remote."""

    with tmp() as t:
        remote_path = t / "remote"
        local_path = t / "local"
        remote_path.mkdir()
        local_path.mkdir()

        for path in [remote_path, local_path]:
            subprocess.run("git init -q", check=True, shell=True, cwd=path)
            subprocess.run(
                "git config set advice.detachedHead false", check=True, shell=True, cwd=path
            )

        remote = Git(remote_path)
        local = Git(local_path)
        local.s(f"git remote add origin {remote_path}")

        yield local, remote


@pytest.fixture(scope="function")
def repo() -> Iterator[Git]:
    with tmp() as path:
        subprocess.run("git init -q", check=True, shell=True, cwd=path)
        subprocess.run(
            "git config set advice.detachedHead false", check=True, shell=True, cwd=path
        )
        yield Git(path)
