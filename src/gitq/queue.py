from dataclasses import dataclass, field
from typing import List, Self, IO, Iterator
from io import StringIO
from pathlib import Path

import yaml

from .git import Git, Commit, GitFailed, UserError
from .continuations import EditBranch, PickCherries
from .yaml import YAMLObject, BaseLoader


class Loader(BaseLoader):
    pass


class Dumper(yaml.Dumper):
    pass


@dataclass
class Baseline(YAMLObject):
    yaml_loader = Loader
    yaml_dumper = Dumper
    sha: str
    ref: str | None = field(default=None)
    remote: str | None = field(default=None)


yaml.add_path_resolver("!QueueFile", [], Loader=Loader, Dumper=Dumper)
yaml.add_path_resolver("!Baseline", ["baselines", None], Loader=Loader, Dumper=Dumper)

# yaml.add_path_resolver("!QueueFile", [], Loader=Loader)
# yaml.add_path_resolver("!Baseline", ["baselines", None], Loader=Loader)


@dataclass
class QueueFile(YAMLObject):
    yaml_loader = Loader
    yaml_dumper = Dumper
    title: str | None = field(default=None)
    description: str | None = field(default=None)
    baselines: List[Baseline] = field(default_factory=list)

    def dump(self, f: IO):
        yaml.dump(self, f, Dumper)

    def dumps(self) -> str:
        with StringIO() as f:
            yaml.dump(self, f, Dumper)
            return f.getvalue()

    @classmethod
    def load(cls, f: IO) -> Self:
        return yaml.load(f, Loader=Loader)

    @classmethod
    def loads(cls, s: str) -> Self:
        with StringIO(s) as f:
            return yaml.load(f, Loader=Loader)


def message(m: str, title: str | None):
    trailers = "Tool: gitq"
    if title:
        return f"{m}: {title}\n\n{trailers}"
    else:
        return f"{m}\n\n{trailers}"


def from_this_tool(c: Commit) -> bool:
    return c.message.rstrip().endswith("\nTool: gitq")


class NotAQueue(UserError):
    pass


class Queue:

    git: Git
    q: QueueFile

    queuefile_name = ".git-queue"

    @property
    def queuefile_path(self) -> Path:
        return self.git.directory / self.queuefile_name

    def __init__(self, git: Git, *, q: QueueFile | None = None):
        self.git = git
        if q:
            self.q = q
        else:
            if not self.queuefile_path.exists():
                raise NotAQueue("This branch is not a queue.")
            with open(self.queuefile_path, "r") as f:
                self.q = QueueFile.load(f)

    def save_queuefile(self, *, amend: bool):
        assert amend
        with open(self.queuefile_path, "w") as f:
            self.q.dump(f)
        self.git("add", self.queuefile_path)
        self.git("commit", "--amend", "-C", "HEAD")

    def merge_baselines(self) -> Commit:

        baseline, *baselines = self.q.baselines
        assert baseline.sha

        self.git.checkout(baseline.sha)

        if not baselines:
            self.git("commit", "--allow-empty", "-m", message("baseline", self.q.title))
            self.save_queuefile(amend=True)
            return self.git.commit("HEAD")

        refs = [b.sha for b in baselines]

        try:
            self.git("merge", "--no-ff", *refs, "-m", message("merged baselines", self.q.title))
        except GitFailed:
            if (self.git.gitdir / "MERGE_HEAD").exists():
                self.git("merge", "--abort")
        else:
            self.save_queuefile(amend=True)
            return self.git.commit("HEAD")

        for ref in refs:
            try:
                self.git.cmd(["git", "merge", "--no-ff", ref])
            except GitFailed:
                if (self.git.gitdir / "MERGE_HEAD").exists():
                    self.git("merge", "--abort")
                raise

        self.save_queuefile(amend=True)
        return self.git.commit("HEAD")

    def init(self):
        self.git("commit", "--allow-empty", "-m", message("initialized queue", self.q.title))
        self.save_queuefile(amend=True)

    def init_new_branch(self, branch: str):
        self.git.detach()
        self.merge_baselines()
        self.git("branch", branch, "HEAD")
        self.git.checkout(branch)

    def duplicates(self, branch: str, base: str, new_base: str) -> Iterator[str]:
        """
        Yield commits in base..branch which are cherry-picked into new_base
        """
        for line in self.git("cherry", new_base, branch, base, quiet=True).strip().splitlines():
            sign, sha = line.split(" ", 1)
            if sign == "-":
                yield sha

    def find_patches(
        self, branch: str, baselines: List[Baseline], new_base: str
    ) -> Iterator[Commit]:
        if self.git.on_orphan_branch():
            return
        commits = self.git.commits(*(f"^{b.sha}" for b in baselines), branch, reverse=True)
        base = self.find_baseline(commits)
        dups = set(self.duplicates(branch, base, new_base))
        for commit in commits:
            if commit.sha in dups:
                continue
            if from_this_tool(commit):
                continue
            if commit.is_merge:
                if self.git.is_conflicted(commit):
                    continue
                else:
                    raise UserError("rebasing merges is not implemented yet")
            changed = self.git("show", "--name-only", "--pretty=", commit.sha, quiet=True).strip()
            if changed == self.queuefile_name:
                continue
            yield commit

    def baselines_for_swap(self) -> Iterator[str]:
        "return a list of shas that git-swap should not proceed past"
        for b in self.q.baselines:
            yield b.sha
        commits = self.git.commits(*(f"^{b.sha}" for b in self.q.baselines), "HEAD", reverse=True)
        for commit in commits:
            if from_this_tool(commit):
                yield commit.sha

    def find_baseline(self, commits: List[Commit]) -> str:
        merges = [c.sha for c in commits if from_this_tool(c)]
        bases = self.git("merge-base", "--independent", *merges, quiet=True).strip().splitlines()
        if len(bases) > 1:
            # TODO make a throwaway merge here
            raise NotImplementedError
        [base] = bases
        return base

    def rebase(self, onto: List[Baseline] | None = None) -> None:
        old_baselines = self.q.baselines
        if onto is None:
            onto = self.q.baselines
        self.q.baselines = [refresh_baseline(b, git=self.git) for b in onto]
        with EditBranch(message="git-queue rebase") as branch:
            self.merge_baselines()
            patches = list(self.find_patches(branch, old_baselines, "HEAD"))
            with PickCherries(cherries=[b.sha for b in patches], edit=True):
                pass


# TODO  check if baseline branches are queues that themselves need refresh


def refresh_baseline(baseline: Baseline, *, git: Git) -> Baseline:
    if baseline.ref is None:
        return baseline
    elif baseline.remote:
        if baseline.ref.startswith("refs/heads/") and (remote := git.find_remote(baseline.remote)):
            git.cmd(["git", "fetch", remote])
            branch = baseline.ref.removeprefix("refs/heads/")
            fetched = f"refs/remotes/{remote}/{branch}"
        else:
            git.cmd(["git", "fetch", baseline.remote, baseline.ref])
            fetched = "FETCH_HEAD"
        return Baseline(git.commit(fetched).sha, baseline.ref, baseline.remote)
    else:
        return Baseline(git.commit(baseline.ref).sha, baseline.ref, None)
