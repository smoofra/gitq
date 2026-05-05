import os
import subprocess
import re
import email
from typing import List, Iterator, NamedTuple, Set, Iterable, Tuple, Optional, Any
from pathlib import Path
from contextlib import contextmanager
from functools import cache
from tempfile import NamedTemporaryFile

from .output import Output


FNULL = open(os.devnull, "w")


class GitFailed(Exception):

    def __init__(self, message: str, *, rc: int):
        super().__init__(message)
        self.rc = rc


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


class DupRecord(NamedTuple):
    "a record output by `git cherry`"

    is_new: bool
    sha: "Sha"

    @property
    def is_duplicate(self):
        return not self.is_new


def coalesce(lines: Iterable[str]) -> Iterator[str]:
    cur = None
    for line in lines:
        if cur is None:
            cur = line
            continue
        if line.startswith(" "):
            cur += line
            continue
        yield cur
        cur = line
    if cur is not None:
        yield cur


class StatusEntry(NamedTuple):
    staged: str  # index status char (' ', 'M', 'A', 'D', 'R', 'C', 'U', '?', '!')
    unstaged: str  # working tree status char
    path: str
    orig_path: Optional[str]  # set for renames/copies


def parse_porcelain_z(output: str) -> Iterator[StatusEntry]:
    tokens = iter(output.split("\0"))
    for entry in tokens:
        if not entry:
            continue
        staged, unstaged, path = entry[0], entry[1], entry[3:]
        orig_path = None
        if staged in ("R", "C"):
            orig_path = next(tokens)
        yield StatusEntry(staged, unstaged, path, orig_path)


class Sha(str):
    def __new__(cls, value: str) -> "Sha":
        if not re.match(r"^[0-9a-f]+$", value, flags=re.IGNORECASE):
            breakpoint()
        return super().__new__(cls, value)


Ref = str  # fully qualified ref name

Branch = str  # branch name WITHOUT refs/heads/ prefix


class Commit(object):

    parents: List[Sha]
    git: "Git"

    def __init__(self, *, log: str, git: "Git"):
        self.git = git
        self.parents = list()
        (headers, message) = log.split("\n\n", 1)
        for header in coalesce(headers.split("\n")):
            (key, value) = header.strip().split(" ", 1)
            if key == "commit":
                self.sha = Sha(value)
            if key == "parent":
                self.parents.append(Sha(value))
            if key == "tree":
                self.tree = Sha(value)
            if key == "author":
                self.author = value
            if key == "committer":
                self.committer = value
            if key == "gpgsig":
                continue
        assert message.endswith("\n")
        lines = message[:-1].split("\n")
        assert all(x.startswith("    ") for x in lines)
        lines = [x[4:] for x in lines]
        self.message = "\n".join(lines) + "\n"

    @property
    def abbrev(self):
        return self.git.abbrev(self.sha)

    @property
    def summary(self) -> str:
        return f"{self.abbrev} {self.title}"

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def title(self) -> str:
        return self.message.split("\n", 1)[0]

    def __str__(self) -> str:
        return self.sha[:10]

    def make_patch_file(self, directory: Path, *, index: Tuple[int, int] | None = None) -> Path:
        format = (
            "From %H Mon Sep 17 00:00:00 2001"
            + "%nFrom: %aN <%aE>"
            + "%nDate: %aD"
            + "%nSubject: [PATCH] %s"
            + "%nGitQ-Committer: %cN <%cE>"
            + "%nGitQ-CommitterDate: %cD"
            + "%nGitQ-Parents: %P"
            + "%n%n%b"
        )
        filename = self.git("log", "-1", "--format=%f", self.sha, quiet=True).strip() + ".patch"
        if index is not None:
            i, num_digits = index
            filename = f"{i:0{num_digits}d}-{filename}"
        path = directory / filename
        with open(path, "wt") as f:
            cmd = ["git", "show", "--diff-merges=first-parent", "--format=" + format, self.sha]
            subprocess.run(cmd, check=True, stdout=f, cwd=self.git.directory, env=self.git.env)
        return path

    def trailers(self) -> dict[str, str]:
        "parse RFC822 trailers from commit message"
        paragraphs = self.message.strip().split("\n\n")
        if not paragraphs:
            return {}
        return dict(email.message_from_string(paragraphs[-1]).items())

    def __hash__(self):
        return hash(self.sha)

    def __eq__(self, other):
        return self.sha == other.sha


class Patch(Commit):
    "A commit treated as a patch for purposes of hashing and comparison"

    patch_id: str

    def __init__(self, *, log: str, git: "Git"):
        super().__init__(log=log, git=git)
        self.patch_id = git.patch_id(self.sha)

    def __hash__(self):
        return hash(self.patch_id)

    def __eq__(self, other):
        try:
            other_patch_id = other.patch_id
        except AttributeError:
            return False
        return self.patch_id == other_patch_id


class Git:

    gitdir: Path  # .git
    directory: Path  # toplevel, and where commands are run from
    fetched: Set[str]
    env: dict[str, str]

    def __init__(self, directory=None, *, env=None):
        self.env = dict(os.environ if env is None else env)
        self.fetched = set()
        self.directory = Path(directory or ".")
        try:
            top = self("rev-parse", "--show-toplevel", quiet=True).strip()
        except GitFailed as e:
            raise UserError("Error: not a git repository") from e
        if not top:
            raise UserError("Error: cannot find working directory.  bare repository?")
        self.directory = Path(top)
        self.gitdir = self.directory / self("rev-parse", "--git-dir", quiet=True).strip()

    def abbrev_if_sha(self, x) -> str:
        if isinstance(x, Sha):
            return self.abbrev(x)
        else:
            return str(x)

    def cmd(
        self, cmd, *, quiet: bool = False, interactive: bool = False, comment: str = ""
    ) -> str:
        if not quiet:
            Output.log_cmd(list(map(self.abbrev_if_sha, cmd)), comment=comment)
        kw: dict[str, Any] = {"env": self.env}
        if interactive:
            kw["stderr"] = subprocess.PIPE
        else:
            kw["stdin"] = FNULL
            kw["stdout"] = subprocess.PIPE
            kw["stderr"] = subprocess.STDOUT
        proc = subprocess.Popen(cmd, cwd=self.directory, encoding="utf8", **kw)
        (out, err) = proc.communicate()
        if proc.wait() != 0:
            err = ((out or "") + (err or "")).strip()
            err, _ = re.subn(r"^", "\t", err, flags=re.MULTILINE)
            raise GitFailed(f"git failed:\n{err}", rc=proc.wait())
        return out

    def __call__(self, *args, quiet: bool = False, comment: str = "") -> str:
        return self.cmd(["git", *args], quiet=quiet, comment=comment)

    def cmd_test(self, args) -> bool:
        proc = subprocess.Popen(
            args,
            cwd=self.directory,
            stdin=FNULL,
            stdout=FNULL,
            stderr=FNULL,
            env=self.env,
        )
        if proc.wait() not in [0, 1]:
            raise GitFailed("git failed", rc=proc.wait())
        return not proc.wait()

    def rev_parse(self, commit: str) -> Sha:
        return Sha(self.cmd(["git", "rev-parse", commit], quiet=True).strip())

    def symbolic_full_name(self, commit: str) -> str | None:
        name = self.cmd(["git", "rev-parse", "--symbolic-full-name", commit], quiet=True).strip()
        return name or None

    def detach(self) -> None:
        self.cmd(["git", "checkout", self.rev_parse("HEAD")], comment="detach")

    def upstream(self, branch: str) -> Sha | None:
        "return the sha of the branch's upstream, or None"
        try:
            return self.rev_parse(branch + "@{upstream}")
        except GitFailed as e:
            errors = ["no upstream configured for branch", "HEAD does not point to a branch"]
            if any(s in str(e) for s in errors):
                return None
            raise

    def head(self) -> Ref | Sha:
        try:
            return self.cmd(["git", "symbolic-ref", "HEAD"], quiet=True).strip()
        except GitFailed:
            return self.rev_parse("HEAD")

    def branch(self) -> Branch | None:
        head = self.head()
        if head.startswith("refs/heads/"):
            return head.removeprefix("refs/heads/")
        return None

    def force_checkout(self, branch: str, comment: str = "") -> None:
        self.cmd(["git", "checkout", "-f", branch], comment=comment)

    def commit(self, ref: str) -> Commit:
        log = self.cmd("git log -n1 --no-notes --pretty=raw".split() + [ref, "--"], quiet=True)
        return Commit(log=log, git=self)

    def commits(self, *refs: str, reverse: bool = False) -> List[Commit]:
        cmd = ["git", "log", "--topo-order", "-z", "--no-notes", "--pretty=raw"]
        if reverse:
            cmd.append("--reverse")
        cmd.extend(refs)
        cmd.append("--")
        logs = self.cmd(cmd, quiet=True)
        return [Commit(log=log, git=self) for log in logs.split("\x00") if log]

    def patches_and_merges(self, *refs: str, reverse: bool = False) -> List[Patch | Commit]:
        commits = self.commits(*refs, reverse=reverse)
        for commit in commits:
            if not commit.is_merge:
                commit.patch_id = self.patch_id(commit.sha)  # type: ignore
                commit.__class__ = Patch
        return commits

    def patches(self, *refs: str, reverse: bool = False) -> List[Patch]:
        return [c for c in self.patches_and_merges(*refs, reverse=reverse) if isinstance(c, Patch)]

    def checkout(self, branch: str, *, comment: str = "", orphan: bool = False) -> None:
        if orphan:
            cmd = ["git", "checkout", "--orphan", branch]
        else:
            cmd = ["git", "checkout", branch]
        self.cmd(cmd, comment=comment)

    @property
    def continuation(self) -> Path:
        return self.gitdir / "continuation.yaml"

    def is_clean(self) -> bool:
        if self("diff-files", "--name-only", quiet=True):
            return False
        if self.on_orphan_branch():
            return True
        return not self("diff-index", "--cached", "--name-only", "HEAD", quiet=True)

    @property
    def cherry_pick_in_progress(self) -> bool:
        return (self.gitdir / "CHERRY_PICK_HEAD").exists()

    @property
    def merge_in_progress(self) -> bool:
        return (self.gitdir / "MERGE_HEAD").exists()

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

    def branch_exists(self, branch: Branch) -> bool:
        return self.ref_exists(f"refs/heads/{branch}")

    def ls_files(self, *args) -> Iterator[str]:
        for line in self.cmd(["git", "ls-files", *args], quiet=True).splitlines():
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
        Output.log_cmd("git ls-files -z | xargs -0 rm")
        for file in self.ls_files():
            path = self.directory / file
            if os.path.exists(path):
                os.unlink(path)
        self.cmd(["git", "read-tree", "--empty"])

    def cherry_pick_abort(self) -> None:
        if self.cherry_pick_in_progress:
            if self.on_orphan_branch():
                Output.log_cmd(["rm", self.gitdir / "CHERRY_PICK_HEAD"])
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
        for line in self.cmd(["git", "remote", "-v"], quiet=True).splitlines():
            name, urlpart = line.rstrip().split("\t")
            if urlpart == f"{url} (fetch)":
                return name
        return None

    def fetch(self, remote: str):
        if remote in self.fetched:
            return
        self.cmd(["git", "fetch", remote])
        self.fetched.add(remote)

    def is_conflicted(self, commit: Commit) -> bool:
        if len(commit.parents) < 2:
            return False
        current: str = commit.parents[0]
        for i, parent in enumerate(commit.parents[1:], 1):
            tree, conflicts = self.merge_tree(current, parent)
            if conflicts:
                return True
            if i < len(commit.parents) - 1:  # temp commit so git merge-tree can see ancestry
                t = self("commit-tree", tree, "-p", current, "-p", parent, "-m", "tmp", quiet=True)
                current = Sha(t.strip())
            else:
                current = tree
        return not self.cmd_test(["git", "diff", "--quiet", commit.sha, current, "--"])

    def merge_tree(self, a: str, b: str) -> Tuple[Sha, Set[str]]:
        cmd = ["git", "merge-tree", "--name-only", "--no-messages", "-z", a, b]
        p = subprocess.run(cmd, cwd=self.directory, capture_output=True, text=True, env=self.env)
        if p.returncode not in [0, 1]:
            raise GitFailed(f"git failed:\n{p.stderr}", rc=p.returncode)
        tree, *conflicts = p.stdout.rstrip("\x00").split("\x00")
        assert (p.returncode == 0) == (not conflicts)
        return Sha(tree), set(conflicts)

    def checkout_tree(self, tree: Sha) -> None:
        "replace index and working files with the specified tree"
        deleted = self("diff", "--diff-filter=A", "--name-only", tree, quiet=True).splitlines()
        self("read-tree", tree)
        self("checkout", "--", ".")
        for rel in deleted:
            (self.directory / rel).unlink()

    def find_duplicates(self, base: str | None, branch: str, onto: str) -> Iterator[DupRecord]:
        "Determine which commits in base..branch are cherry-picked in onto"
        if base is None:
            output = self("cherry", onto, branch, quiet=True)
        else:
            output = self("cherry", onto, branch, base, quiet=True)
        for line in output.strip().splitlines():
            sign, sha = line.split(" ", 1)
            assert sign in "-+"
            yield DupRecord(sign == "+", Sha(sha))

    def is_ancestor(self, ancestor: str, of: str = "HEAD") -> bool:
        "Return True if ancestor is reachable from descendant"
        return self.cmd_test(["git", "merge-base", "--is-ancestor", ancestor, of])

    def all_merge_bases(self, *refs: Sha, none_ok: bool = False) -> List[Sha]:
        cmd = ["git", "merge-base", "--all", *refs]
        proc = subprocess.run(
            cmd, cwd=self.directory, env=self.env, capture_output=True, text=True
        )
        if proc.returncode == 0:
            return [Sha(x) for x in proc.stdout.strip().split()]
        elif proc.returncode == 1 and none_ok:
            return []
        else:
            raise GitFailed(f"git merge-tree failed:\n{proc.stderr}", rc=proc.returncode)

    def independent(self, *shas: Sha) -> List[Sha]:
        out = self("merge-base", "--independent", *shas, quiet=True)
        return [Sha(x) for x in out.strip().splitlines()]

    @cache
    def abbrev(self, ref: Sha) -> str:
        "Return abbreviated sha for ref"
        return self.cmd(["git", "rev-parse", "--short", ref], quiet=True).strip()

    def abbrev_symbolic(self, ref: str) -> str:
        "Return an abbreviated ref name for ref"
        cmd = ["git", "rev-parse", "--symbolic", "--abbrev-ref", ref]
        if abbrev := self.cmd(cmd, quiet=True).strip():
            return abbrev
        return ref

    @contextmanager
    def temp_index_and_files(self):
        if not self.is_clean():
            Output.print(self("status"))
            raise Exception("repo is not clean")
        try:
            yield
        finally:
            cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=A"]
            new_files = self.cmd(cmd, quiet=True).strip().splitlines()
            self.cmd(["git", "reset", "--hard", "HEAD"])
            for f in new_files:
                path = self.directory / f
                if path.exists():
                    path.unlink()

    def status(self) -> Iterator[StatusEntry]:
        out = self.cmd(["git", "status", "--porcelain", "-z"], quiet=True)
        yield from parse_porcelain_z(out)

    def dirty_files(self) -> Iterator[str]:
        for line in self.status():
            if line.staged == "?" and line.unstaged == "?":
                continue
            yield line.path
            if line.orig_path is not None:
                yield line.orig_path

    @contextmanager
    def temp_index(self, tree: Sha | None = None) -> Iterator["Git"]:
        if tree is None:
            tree = Sha(self.cmd(["git", "write-tree"], quiet=True).strip())
        with NamedTemporaryFile(suffix=".git-index") as f:
            env = {**self.env, "GIT_INDEX_FILE": f.name}
            git = Git(self.directory, env=env)
            git.cmd(["git", "read-tree", tree], quiet=True)
            yield git

    @contextmanager
    def temp_env(self, **kw) -> Iterator["Git"]:
        "yield a new git wrapper with a fresh environment that that is safe to mutate."
        yield type(self)(self.directory, env={**self.env, **kw})

    @cache
    def patch_id(self, sha: Sha) -> str:
        "Return the patch-id for a commit, or None if the commit has no diff."
        diff = self("show", "--no-notes", sha, "--", quiet=True)
        proc = subprocess.Popen(
            ["git", "patch-id", "--stable"],
            cwd=self.directory,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(diff)
        if proc.returncode != 0:
            raise GitFailed(f"git patch-id failed:\n{stderr}", rc=proc.returncode)
        patch_id, _ = stdout.strip().split()
        return patch_id

    def apply_patch_to_index(self, commit: Commit, *, reverse: bool = False) -> None:
        "Apply (or reverse-apply) a commit's diff to the index"
        if len(commit.parents) > 1:
            raise Exception(f"commit {commit.summary} is not a patch")
        diff = self("show", "--no-notes", commit.sha, "--", quiet=True)
        if not diff:
            return
        cmd = ["git", "apply", "--cached"]
        if reverse:
            cmd.append("--reverse")
        proc = subprocess.Popen(
            cmd,
            cwd=self.directory,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _, stderr = proc.communicate(diff)
        if proc.returncode != 0:
            raise GitFailed(f"git apply failed\n{stderr}", rc=proc.returncode)

    def check_apply_patch_to_index(self, commit: Commit, *, reverse: bool = False) -> bool:
        "Apply (or reverse-apply) a commit's diff to the index"
        if len(commit.parents) > 1:
            raise Exception(f"commit {commit.summary} is not a patch")
        diff = self("show", "--no-notes", commit.sha, "--", quiet=True)
        if not diff:
            return True
        cmd = ["git", "apply", "--cached", "--check"]
        if reverse:
            cmd.append("--reverse")
        proc = subprocess.Popen(
            cmd,
            cwd=self.directory,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _, stderr = proc.communicate(diff)
        if proc.returncode not in [0, 1]:
            raise GitFailed(f"git apply failed\n{stderr}", rc=proc.returncode)
        return proc.returncode == 0

    def check_apply(self, commit: Commit, *, to: Sha, reverse: bool = False) -> bool:
        with self.temp_index(tree=to):
            return self.check_apply_patch_to_index(commit, reverse=reverse)
