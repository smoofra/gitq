from dataclasses import dataclass, field
from typing import List, Iterator
from io import StringIO
from pathlib import Path

import yaml

from .output import Output
from .git import Git, Commit, GitFailed, UserError, contextGit
from .continuations import (
    EditBranch,
    PickCherries,
    Step,
    Then,
    CheckoutBranch,
    progn,
    Loader as ContinuationsLoader,
    Dumper as ContinuationsDumper,
)
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


# FIXME this was used improperly as a test for baseline commits. Check
# existing callers to figure out what they really mean by it and make it
# more specific.


def from_this_tool(c: Commit) -> bool:
    return c.message.rstrip().endswith("\nTool: gitq")


def is_merged_baseline(c: Commit) -> bool:
    m = c.message.strip()
    return m.endswith("\nTool: gitq") and (
        m.startswith("baseline") or m.startswith("merged baselines")
    )


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

    def save_queuefile(self, *, amend: bool = False, message: str = ""):
        assert amend ^ bool(message)
        with open(self.queuefile_path, "w") as f:
            yaml.dump(self.qf, f, Dumper=Dumper)
        self.git("add", self.queuefile_path.relative_to(self.git.directory))
        if amend:
            self.git("commit", "--amend", "-C", "HEAD")
        else:
            self.git("commit", "-m", message)

    def init(self):
        self.git("commit", "--allow-empty", "-m", message("initialized queue", self.qf.title))
        self.save_queuefile(amend=True)

    def init_new_branch(self, branch: str):
        self.git.detach()
        self.save_queuefile(message="new queue branch")
        progn(MergeBaselines(self.qf.baselines), NewBranch(branch))

    def find_patches(self, ref: str, baselines: List[Baseline], new_base: str) -> Iterator[Commit]:
        if self.git.on_orphan_branch():
            return
        commits = self.git.commits(*(f"^{b.sha}" for b in baselines), ref, reverse=True)
        base = self.find_git_cherry_limit(commits)
        # We use the + side instead of the - side of the `git cherry`
        # output to detect duplicates, because if we used the - side, then
        # it would only filter out distinct (different sha) commits that
        # are duplicated, but it does not filter out commits that are
        # literally present (same sha) in both branch and new_base.
        new = set(r.sha for r in self.git.find_duplicates(base, ref, new_base) if r.is_new)
        for commit in commits:
            if from_this_tool(commit):
                continue
            if commit.is_merge:
                if self.git.is_conflicted(commit):
                    raise UserError(f"rebasing merges is not implemented yet: {commit.summary}")
                continue
            if commit.sha not in new:
                continue
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

    def find_git_cherry_limit(self, commits: List[Commit]) -> str | None:
        "Find the 'baseline' or 'merged baselines' commit in the queue"
        merges = [c.sha for c in commits if is_merged_baseline(c)]
        if len(merges) == 0:
            # See below, just pick some limit.  Can't return them all
            return commits[0].parents[0] if commits[0].parents else None
        bases = self.git("merge-base", "--independent", *merges, quiet=True).strip().splitlines()
        # This is only used to provide a limit to `git cherry`.   If there
        # are multiple baselines, then `git cherry` may produce additional
        # output for baseline commits that should have been excluded.   But
        # that does not actually matter much, because `git cherry` is only
        # used to filter out commits from the list produced by `git log`,
        # and `git log` can take multiple limits.
        #
        # Alternatively, we could create a throwaway merge here and use
        # that as the limit.
        return bases[0]

    def rebase(self, onto: List[Baseline] | None = None) -> None:
        with Output.heading("rebasing"):
            Rebase(onto).run()

    # TODO if baseline is a remote branch, but there is a local branch
    # tracking it, detect that.

    @classmethod
    def needs_rebase(cls, ref: str | None) -> bool:
        "Return True if the local queue branch at ref has baselines that have been updated."
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
    "Temporarily checkout a the specified branch and rebase it."

    ref: str

    def run(self):
        with Output.heading(f"rebasing branch {self.ref}"), CheckoutBranch(self.ref):
            Rebase().run()


@dataclass
class RebaseOne(Step):
    "Rebase a single branch (not recursive)."

    onto: List[Baseline] | None

    def run(self):
        q = Queue(self.git)

        old_baselines = q.qf.baselines
        if self.onto is None:
            self.onto = q.qf.baselines

        # FIXME these should not be re-refreshed every time this resumes
        q.qf.baselines = [refresh_baseline(b, git=self.git) for b in self.onto]
        with EditBranch(message="git-queue rebase") as branch:
            progn(MergeBaselines(q.qf.baselines), FindAndPickCherries(branch, old_baselines))


@dataclass
class FindAndPickCherries(Step):
    "Find patches in branch, and thn apply them to HEAD"

    branch: str
    old_baselines: List[Baseline]

    def run(self) -> None:
        q = Queue(self.git)
        patches = list(q.find_patches(self.branch, self.old_baselines, "HEAD"))
        with PickCherries(cherries=[b.sha for b in patches], edit=True):
            pass


@dataclass
class NewBranch(Step):
    name: str

    def run(self) -> None:
        self.git("branch", self.name, "HEAD")
        self.git.checkout(self.name)


@dataclass
class Rebase(Step):
    """
    Recursively rebase a queue branch
      * First, rebase any baselines which are also queue branches
      * Then rebase the current branch
    """

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


@dataclass
class MergeBaselines(Step):

    baselines: List[Baseline]

    def run(self) -> None:
        with Output.heading("merge baselines"):
            self.merge_baselines()

    def merge_baselines(self) -> None:
        q = Queue(self.git)
        q.qf.baselines = self.baselines

        baseline, *baselines = self.baselines
        assert baseline.sha

        self.git.checkout(baseline.sha, comment="baseline" if not baselines else "first baseline")

        if not baselines:
            self.git("commit", "--allow-empty", "-m", message("baseline", q.qf.title))
            q.save_queuefile(amend=True)
            return

        refs = [b.sha for b in baselines]

        m = message("merged baselines", q.qf.title)

        # try octopus merge first
        try:
            self.git("merge", "--no-ff", *refs, "-m", m)
        except GitFailed:
            if (self.git.gitdir / "MERGE_HEAD").exists():
                if self.git.unmerged_files() == {q.queuefile_name}:
                    q.save_queuefile(message=m)
                    return
                self.git("merge", "--abort")
        else:
            q.save_queuefile(amend=True)
            return

        # merge one at a time
        for ref in refs:
            try:
                self.git("merge", "--no-ff", ref, "-m", m)
            except GitFailed:
                if not (self.git.gitdir / "MERGE_HEAD").exists():
                    raise
                if self.git.unmerged_files() == {q.queuefile_name}:
                    q.save_queuefile(message=m)
                    continue
                self.git("merge", "--abort")
                raise

        q.save_queuefile(amend=True)
        return


def refresh_baseline(baseline: Baseline, *, git: Git) -> Baseline:
    if baseline.ref is None:
        return baseline
    elif baseline.remote:
        if baseline.ref.startswith("refs/heads/") and (remote := git.find_remote(baseline.remote)):
            git.fetch(remote)
            branch = baseline.ref.removeprefix("refs/heads/")
            fetched = f"refs/remotes/{remote}/{branch}"
        else:
            git.cmd(["git", "fetch", baseline.remote, baseline.ref])
            fetched = "FETCH_HEAD"
        return Baseline(git.commit(fetched).sha, baseline.ref, baseline.remote)
    else:
        return Baseline(git.commit(baseline.ref).sha, baseline.ref, None)
