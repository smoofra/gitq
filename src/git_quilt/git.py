import os
import subprocess
import shlex
from typing import List, Set

FNULL = open(os.devnull, "w")


class GitFailed(Exception):
    pass


class SwapFailed(Exception):
    pass


class MergeFound(Exception):
    pass


class UserError(Exception):
    pass


class Commit(object):

    parents: List[str]

    def __init__(self, *, log: str):
        self.parents = list()
        (headers, message) = log.split("\n\n", 1)
        for header in headers.split("\n"):
            (key, value) = header.strip().split(" ", 1)
            if key == "commit":
                self.sha = value
            if key == "parent":
                self.parents.append(value)
            if key == "tree":
                self.tree = value
            if key == "author":
                self.author = value
            if key == "committer":
                self.committer = value
        assert message.endswith("\n")
        lines = message[:-1].split("\n")
        assert all(x.startswith("    ") for x in lines)
        lines = [x[4:] for x in lines]
        self.message = "\n".join(lines) + "\n"

    @property
    def summary(self) -> str:
        m, _ = self.message.split("\n", 1)
        return f"{self.sha[:10]} {m}"

    def __str__(self) -> str:
        return self.sha[:10]


class Git:

    def __init__(self, directory=None):
        if directory is None:
            directory = "."
        self.directory = directory
        try:
            self.directory = self.cmd(
                "git rev-parse --show-toplevel".split(), quiet=True, stderr=FNULL
            ).strip()
        except GitFailed as e:
            raise UserError("Error: not a git repository") from e
        if self.directory == "":
            raise UserError("Error: cannot find working directory.  bare repository?")
        self.gitdir = self.cmd("git rev-parse --git-dir".split(), quiet=True).strip()

    def cmd(self, args, *, quiet: bool = False, interactive: bool = False, **kw) -> str:
        if not quiet:
            print("+", " ".join(map(shlex.quote, args)))
        if not interactive:
            kw["stdin"] = FNULL
            kw["stdout"] = subprocess.PIPE
        proc = subprocess.Popen(args, cwd=self.directory, encoding="utf8", **kw)
        (out, err) = proc.communicate()
        if proc.wait() != 0:
            raise GitFailed("git failed")
        return out

    def cmd_test(self, args, **kw) -> bool:
        proc = subprocess.Popen(args, cwd=self.directory, stdin=FNULL, **kw)
        code = proc.wait()
        if code not in [0, 1]:
            raise GitFailed("git failed")
        return not code

    def rev_parse(self, commit: str) -> str:
        return self.cmd(["git", "rev-parse", commit], quiet=True).strip()

    def detach(self) -> None:
        self.cmd(["git", "checkout", self.rev_parse("HEAD")], stderr=FNULL)

    def head(self) -> str:
        try:
            return self.cmd(["git", "symbolic-ref", "HEAD"], quiet=True, stderr=FNULL).strip()
        except GitFailed:
            return self.rev_parse("HEAD")

    def force_checkout(self, branch: str) -> None:
        self.cmd(["git", "checkout", "-f", branch], stderr=FNULL)

    def commit(self, ref: str) -> Commit:
        log = self.cmd("git log -n1 --no-notes --pretty=raw".split() + [ref], quiet=True)
        return Commit(log=log)

    def checkout(self, branch: str) -> None:
        self.cmd(["git", "checkout", branch], stderr=FNULL)

    @property
    def swap_json(self) -> str:
        return os.path.join(self.gitdir, "swap.json")

    def baselines(self, branch: str) -> Set[str]:
        if not branch.startswith("refs/heads/"):
            return set()
        branch = branch.removeprefix("refs/heads/")
        try:
            baseline = self.cmd(["git", "config", f"branch.{branch}.baseline"]).strip()
        except GitFailed:
            return set()
        return {self.rev_parse(baseline)}

    def is_clean(self) -> bool:
        return "" == self.cmd(
            "git diff-index --cached --name-only HEAD".split(), quiet=True
        ) and "" == self.cmd("git diff-files --name-only".split(), quiet=True)

    @property
    def conflicted(self) -> bool:
        return os.path.exists(os.path.join(self.gitdir, "CHERRY_PICK_HEAD"))

    def unique_parent(self, commit: Commit) -> Commit:
        if len(commit.parents) != 1:
            raise MergeFound(f"{commit} is a merge")
        return self.commit(commit.parents[0])
