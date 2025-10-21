import os
import subprocess
import shlex
import re
from typing import List, Iterator, NamedTuple, Set
from pathlib import Path
import sys

FNULL = open(os.devnull, "w")


class GitFailed(Exception):
    pass


class MergeFound(Exception):
    pass


class UserError(Exception):
    pass


class AuthorDate(NamedTuple):
    name: str
    email: str
    date: str


def split_author(line: str) -> AuthorDate:
    m = re.match(r"\s*([^\<\>]+) <([^\<\>]+)> ([\d\-\+\s]+?)\s*$", line)
    assert m
    return AuthorDate(m.group(1), m.group(2), m.group(3))


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

    gitdir: Path
    directory: Path

    def __init__(self, directory=None):
        self.directory = Path(directory or ".")
        try:
            top = self.cmd(
                "git rev-parse --show-toplevel".split(), quiet=True, stderr=FNULL
            ).strip()
        except GitFailed as e:
            raise UserError("Error: not a git repository") from e
        if not top:
            raise UserError("Error: cannot find working directory.  bare repository?")
        self.directory = Path(top)
        self.gitdir = Path(self.cmd("git rev-parse --git-dir".split(), quiet=True).strip())

    def log_cmd(self, cmd: List[str | Path] | str):
        if not isinstance(cmd, str):
            cmd = " ".join(map(lambda x: shlex.quote(str(x)), cmd))
        print("+", cmd)
        sys.stdout.flush()

    def cmd(self, cmd, *, quiet: bool = False, interactive: bool = False, **kw) -> str:
        if not quiet:
            self.log_cmd(cmd)
        if not interactive:
            kw["stdin"] = FNULL
            kw["stdout"] = subprocess.PIPE
            kw["stderr"] = subprocess.PIPE
        proc = subprocess.Popen(cmd, cwd=self.directory, encoding="utf8", **kw)
        (out, err) = proc.communicate()
        err, _ = re.subn(r"^", "\t", err.strip(), flags=re.MULTILINE)
        if proc.wait() != 0:
            raise GitFailed(f"git failed:\n{err}")
        return out

    def __call__(self, *args, quiet: bool = False) -> str:
        return self.cmd(["git", *args], quiet=quiet)

    def cmd_test(self, args, **kw) -> bool:
        proc = subprocess.Popen(
            args, cwd=self.directory, stdin=FNULL, stdout=FNULL, stderr=FNULL, **kw
        )
        code = proc.wait()
        if code not in [0, 1]:
            raise GitFailed("git failed")
        return not code

    def rev_parse(self, commit: str) -> str:
        return self.cmd(["git", "rev-parse", commit], quiet=True).strip()

    def symbolic_full_name(self, commit: str) -> str | None:
        name = self.cmd(["git", "rev-parse", "--symbolic-full-name", commit], quiet=True).strip()
        return name or None

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
        log = self.cmd("git log -n1 --no-notes --pretty=raw".split() + [ref, "--"], quiet=True)
        return Commit(log=log)

    def commits(self, *refs: str, reverse: bool = False) -> List[Commit]:
        if reverse:
            cmd = ["git", "log", "-z", "--no-notes", "--reverse", "--pretty=raw", *refs, "--"]
        else:
            cmd = ["git", "log", "-z", "--no-notes", "--pretty=raw", *refs, "--"]
        logs = self.cmd(cmd, quiet=True)
        return [Commit(log=log) for log in logs.split("\x00") if log]

    def checkout(self, branch: str) -> None:
        self.cmd(["git", "checkout", branch], stderr=FNULL)

    @property
    def continuation(self) -> Path:
        return self.gitdir / "continuation.json"

    def baselines(self, branch: str) -> List[str]:
        if not branch.startswith("refs/heads/"):
            return list()
        branch = branch.removeprefix("refs/heads/")
        try:
            baseline = self.cmd(["git", "config", f"branch.{branch}.baseline"], quiet=True).strip()
        except GitFailed:
            return list()
        return [self.rev_parse(baseline)]

    def is_clean(self) -> bool:
        if self("diff-files", "--name-only", quiet=True):
            return False
        if self.on_orphan_branch():
            return True
        return not self("diff-index", "--cached", "--name-only", "HEAD", quiet=True)

    @property
    def cherry_pick_in_progress(self) -> bool:
        return (self.gitdir / "CHERRY_PICK_HEAD").exists()

    def unique_parent(self, commit: Commit) -> Commit:
        if len(commit.parents) != 1:
            raise MergeFound(f"{commit} is a merge")
        return self.commit(commit.parents[0])

    def unique_parent_or_root(self, commit: Commit) -> Commit | None:
        if len(commit.parents) == 0:
            return None
        else:
            return self.unique_parent(commit)

    def branches(self) -> Iterator[str]:
        for line in self.cmd(["git", "for-each-ref", "refs/heads"], quiet=True).splitlines():
            m = re.search(r"\trefs/heads/(.*?)\s*$", line)
            assert m
            yield m.group(1)

    def ref_exists(self, ref: str) -> bool:
        return self.cmd_test(["git", "rev-parse", "--verify", "--quiet", ref, "--"])

    def branch_exists(self, branch: str) -> bool:
        return self.ref_exists(f"refs/heads/{branch}")

    def ls_files(self) -> Iterator[str]:
        for line in self.cmd(["git", "ls-files"], quiet=True).splitlines():
            yield line.rstrip()

    def on_orphan_branch(self) -> bool:
        """
        Returns true if HEAD points to a branch name which does not yet
        exist. This generally only happens after `git init`, or `git
        checkout --orphan`.
        """
        try:
            head = self.cmd(["git", "symbolic-ref", "HEAD"], quiet=True).strip()
        except GitFailed:
            return False
        return not self.ref_exists(head)

    def delete_index_and_files(self):
        self.log_cmd("git ls-files -z | xargs -0 rm")
        for file in self.ls_files():
            path = self.directory / file
            if os.path.exists(path):
                os.unlink(path)
        self.cmd(["git", "read-tree", "--empty"])

    def cherry_pick_abort(self) -> None:
        if self.cherry_pick_in_progress:
            if self.on_orphan_branch():
                self.log_cmd(["rm", self.gitdir / "CHERRY_PICK_HEAD"])
                (self.gitdir / "CHERRY_PICK_HEAD").unlink()
                self.delete_index_and_files()
            else:
                self.cmd(["git", "cherry-pick", "--abort"])

    def has_unmerged_files(self) -> bool:
        return bool(self.cmd(["git", "ls-files", "--unmerged"], quiet=True).strip())

    def unmerged_files(self) -> Set[str]:
        lines = self.cmd(["git", "ls-files", "--unmerged"], quiet=True).splitlines()
        return {line.strip().split("\t", 1)[1] for line in lines}

    def find_remote(self, url: str) -> str | None:
        for line in self.cmd(["git", "remote", "-v"], quiet=True):
            name, urlpart = line.rstrip().split("\t")
            if urlpart == f"{url} (fetch)":
                return name
        return None
