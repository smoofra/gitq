from dataclasses import dataclass, field
from typing import List, Iterator
from io import StringIO
from pathlib import Path

import yaml

from .output import Output
from .git import Git, Commit, GitFailed, UserError, contextGit
from .continuations import EditBranch, PickCherries, Step, Then, CheckoutBranch
from .continuations import Loader as ContinuationsLoader, Dumper as ContinuationsDumper
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

# Baseline can appear in continuation files (e.g. as RebaseOne.onto), so register it there too.
ContinuationsLoader.add_constructor(Baseline.yaml_tag, Baseline.from_yaml)
ContinuationsDumper.add_representer(Baseline, Baseline.to_yaml)


@dataclass
class QueueFile(YAMLObject):

    yaml_loader = Loader
    yaml_dumper = Dumper

    title: str | None = field(default=None)
    description: str | None = field(default=None)
    baselines: List[Baseline] = field(default_factory=list)


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
    qf: QueueFile

    queuefile_name = ".git-queue"

    @property
    def queuefile_path(self) -> Path:
        return self.git.directory / self.queuefile_name

    def __init__(self, git: Git, *, qf: QueueFile | None = None):
        self.git = git
        if qf:
            self.qf = qf
        else:
            if not self.queuefile_path.exists():
                raise NotAQueue("This branch is not a queue.")
            with open(self.queuefile_path, "r") as f:
                self.qf = yaml.load(f, Loader=Loader)

    def save_queuefile(self, *, amend: bool):
        assert amend
        with open(self.queuefile_path, "w") as f:
            yaml.dump(self.qf, f, Dumper=Dumper)
        self.git("add", self.queuefile_path)
        self.git("commit", "--amend", "-C", "HEAD")

    def merge_baselines(self) -> Commit:

        baseline, *baselines = self.qf.baselines
        assert baseline.sha

        self.git.checkout(baseline.sha, comment="merge_baselines")

        if not baselines:
            self.git("commit", "--allow-empty", "-m", message("baseline", self.qf.title))
            self.save_queuefile(amend=True)
            return self.git.commit("HEAD")

        refs = [b.sha for b in baselines]

        try:
            self.git("merge", "--no-ff", *refs, "-m", message("merged baselines", self.qf.title))
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
        self.git("commit", "--allow-empty", "-m", message("initialized queue", self.qf.title))
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
        for b in self.qf.baselines:
            yield b.sha
        commits = self.git.commits(*(f"^{b.sha}" for b in self.qf.baselines), "HEAD", reverse=True)
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
        with Output.heading("rebasing"):
            Rebase(onto).run()

    @classmethod
    def needs_rebase(cls, ref: str | None) -> bool:
        """Return True if the local queue branch at ref has baselines that have been updated."""
        if ref is None or not ref.startswith("refs/heads/"):
            return False
        git = contextGit.get()
        try:
            content = git("show", f"{ref}:{cls.queuefile_name}", quiet=True)
        except GitFailed:
            return False
        qf = yaml.load(StringIO(content), Loader=Loader)
        for b in qf.baselines:
            if refresh_baseline(b, git=git).sha != b.sha:
                return True
        return False


@dataclass
class RebaseBranch(Step):
    ref: str

    def run(self):
        with Output.heading(f"rebasing branch {self.ref}"), CheckoutBranch(self.ref):
            Rebase().run()


@dataclass
class RebaseOne(Step):

    onto: List[Baseline] | None

    def run(self):
        q = Queue(self.git)

        old_baselines = q.qf.baselines
        if self.onto is None:
            self.onto = q.qf.baselines

        q.qf.baselines = [refresh_baseline(b, git=self.git) for b in self.onto]
        with EditBranch(message="git-queue rebase") as branch:
            q.merge_baselines()
            patches = list(q.find_patches(branch, old_baselines, "HEAD"))
            with PickCherries(cherries=[b.sha for b in patches], edit=True):
                pass


@dataclass
class Rebase(Step):

    onto: None | List[Baseline] = field(default=None)

    def run(self) -> None:
        steps: List[Step] = list()

        q = Queue(self.git)
        for b in q.qf.baselines:
            if q.needs_rebase(b.ref):
                assert b.ref
                steps.append(RebaseBranch(b.ref))

        steps.append(RebaseOne(onto=self.onto))

        with Then(steps=steps):
            pass


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
