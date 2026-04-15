from dataclasses import dataclass, field
from typing import List, Iterator
from io import StringIO
from pathlib import Path
from contextlib import contextmanager
from functools import cached_property

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
    Continuation,
    Suspend,
    Resume,
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


@dataclass
class QueueFile(YAMLObject):

    yaml_loader = Loader
    yaml_dumper = Dumper

    title: str | None = field(default=None)
    description: str | None = field(default=None)
    baselines: List[Baseline] = field(default_factory=list)


# These can appear in continuation files (e.g. as RebaseOne.onto), so register there too.
Continuation.register(Baseline)
Continuation.register(QueueFile)


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

    def save_queuefile(
        self, *, amend: bool = False, commit_message: str = "", stage: bool | None = None
    ):
        assert amend + (stage is not None) + bool(commit_message) == 1
        with open(self.queuefile_path, "w") as f:
            yaml.dump(self.qf, f, Dumper=Dumper)
        if stage or amend or commit_message:
            self.git("add", self.queuefile_path.relative_to(self.git.directory))
        if amend:
            self.git("commit", "--amend", "--allow-empty", "-C", "HEAD")
        elif commit_message:
            self.git("commit", "--allow-empty", "-m", commit_message)

    def init(self):
        self.git("commit", "--allow-empty", "-m", message("initialized queue", self.qf.title))
        self.save_queuefile(amend=True)

    def init_new_branch(self, branch: str):
        self.git.detach()
        self.save_queuefile(commit_message="new queue branch")
        progn(MergeBaselines(self.qf), NewBranch(branch))

    @staticmethod
    def find_user_merges(commits: List[Commit]) -> Iterator[Commit]:
        """
        Find user merges as any merge commit below the "merged baseline" commits
        Takes list of commits as output by:

           git log --topo-order queue ^baseline...
        """
        # find user merges as any merge commit below the "merged baseline" commits
        baseline_shas: set[str] = set()
        for c in commits:
            if is_merged_baseline(c):
                baseline_shas.add(c.sha)
            if c.sha in baseline_shas:
                for p in c.parents:
                    baseline_shas.add(p)
        for c in commits:
            if c.is_merge and c.sha in baseline_shas and not is_merged_baseline(c):
                yield c

    def find_patches(self, ref: str, baselines: List[Baseline], new_base: str) -> Iterator[Commit]:
        if self.git.on_orphan_branch():
            return
        commits = self.git.commits(*(f"^{b.sha}" for b in baselines), ref, reverse=True)
        user_merges = {c.sha for c in self.find_user_merges(list(reversed(commits)))}
        base = self.find_git_cherry_limit(commits)
        # We use the + side instead of the - side of the `git cherry`
        # output to detect duplicates, because if we used the - side, then
        # it would only filter out distinct (different sha) commits that
        # are duplicated, but it does not filter out commits that are
        # literally present (same sha) in both branch and new_base.
        new = set(r.sha for r in self.git.find_duplicates(base, ref, new_base) if r.is_new)
        for commit in commits:
            if commit.sha in user_merges:
                continue
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
            progn(MergeBaselines(q.qf), FindAndPickCherries(branch, old_baselines))


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
class MergeContinue(Continuation):
    """
    When resuming, check if the user ran `git commit`, and do it for them
    if they haven't.
    """

    @contextmanager
    def impl(self) -> Iterator:
        try:
            yield
        except (Exception, Resume):
            self.git("merge", "--abort")
            raise
        if self.git.merge_in_progress:
            if self.git.has_unmerged_files():
                Output.print("The index still has unmerged files.")
                raise Suspend(status="resolve conflicts and continue")
            self.git("commit", "--no-edit")


@dataclass
class MergeBaselines(Step, Continuation):

    qf: QueueFile
    user_merges: List[str] = field(default_factory=list)
    find_user_merges: bool = True
    needs_checkout: bool = True
    suspended_at: str | None = None

    def run(self) -> None:
        with self:
            pass

    @contextmanager
    def impl(self) -> Iterator:
        self.check_user_merges()
        yield
        if self.suspended_at:
            # If continued after asking the user to make a merge, pick it
            # up and add it to the list of user merges, and go back to the
            # commit we were at before.
            self.user_merges.append(self.git.rev_parse("HEAD"))
            self.git.checkout(self.suspended_at)
            self.suspended_at = None
        with Output.heading("merge baselines"):
            self.merge_baselines()

    @cached_property
    def q(self):
        return Queue(self.git, qf=self.qf)

    def still_needed(self) -> Iterator[Baseline]:
        "return a list of baselines that have not yet been merged"
        for baseline in self.qf.baselines:
            if not self.git.is_ancestor(baseline.sha):
                yield baseline

    @cached_property
    def m(self) -> str:
        return message("merged baselines", self.qf.title)

    def check_user_merges(self):
        """
        Find user merges in HEAD.  Ensure that all user merges are clean,
        that is they do not introduce any new commits outside of the
        baselines.
        """
        if not self.find_user_merges:
            return
        self.find_user_merges = False

        # Find user merges in HEAD
        q = Queue(self.git)  # This is the OLD queue, before baselines have been updated
        commits = self.git.commits("HEAD", *(f"^{b.sha}" for b in q.qf.baselines))
        self.user_merges.extend(c.sha for c in Queue.find_user_merges(commits))

        # Check that they're clean, using the NEW queue
        clean = list()
        for u in self.user_merges:
            ancestors = self.git.commits(
                u,
                *(f"^{b.sha}" for b in self.qf.baselines),
            )
            if {a.sha for a in ancestors} <= {u for u in self.user_merges}:
                clean.append(u)
            else:
                Output.print(f"user merge {u} is not clean, can't use it")
        self.user_merges = clean

    def merge_baselines(self) -> None:
        q = self.q

        if len(self.user_merges) > 1:
            self.user_merges = (
                self.git("merge-base", "--independent", *self.user_merges, quiet=True)
                .strip()
                .splitlines()
            )

        # First, check out one of the baselines so there's something to
        # merge into
        if self.needs_checkout:
            self.git.checkout(self.qf.baselines[0].sha, comment="baseline")
            self.needs_checkout = False

        needed = list(self.still_needed())
        if not needed:
            q.save_queuefile(commit_message=message("baseline", q.qf.title))
            return

        # try octopus merge first
        try:
            self.git("merge", "--no-ff", *(b.sha for b in needed), "-m", self.m)
        except GitFailed:
            if (self.git.gitdir / "MERGE_HEAD").exists():
                if self.git.unmerged_files() == {q.queuefile_name}:
                    q.save_queuefile(commit_message=self.m)
                    return
                self.git("merge", "--abort")
        else:
            q.save_queuefile(amend=True)
            return

        # try octopus with user merges
        try:
            self.git("merge", "--no-ff", *(b.sha for b in needed), *self.user_merges, "-m", self.m)
        except GitFailed:
            if (self.git.gitdir / "MERGE_HEAD").exists():
                if self.git.unmerged_files() == {q.queuefile_name}:
                    q.save_queuefile(commit_message=self.m)
                    return
                self.git("merge", "--abort")
        else:
            q.save_queuefile(amend=True)
            return

        # merge one at a time
        while needed:
            baseline = needed.pop(0)
            try:
                self.git("merge", "--no-ff", baseline.sha, "-m", self.m)
            except GitFailed:
                if not self.git.merge_in_progress:
                    raise
                if self.git.unmerged_files() == {q.queuefile_name}:
                    q.save_queuefile(commit_message=self.m)
                    continue
                self.git("merge", "--abort")
            else:
                q.save_queuefile(amend=True)
                continue
            # Oh, no!  A conflict!
            self.resolve_conflicts(baseline)
            needed = list(self.still_needed())

    def would_conflict(self, a: str, b: str) -> bool:
        _, conflicts = self.git.merge_tree(a, b)
        return not (conflicts <= {Queue.queuefile_name})

    def resolve_conflicts(self, baseline: Baseline):

        head = self.git.rev_parse("HEAD")
        to_merge = baseline.sha

        # Try to find a user merge that can resolve the conflict
        for u in self.user_merges:
            contains_baseline = self.git.is_ancestor(baseline.sha, of=u)
            if self.git.rev_parse("HEAD") != head:
                self.git.checkout(head)

            # try to merge u
            try:
                self.git("merge", "--no-ff", u, "-m", self.m)
            except GitFailed:
                if not self.git.merge_in_progress:
                    raise
                if not (self.git.unmerged_files() <= {Queue.queuefile_name}):
                    self.git("merge", "--abort")
                    if contains_baseline:
                        # It looks like the user has already resolved one conflict with
                        # baseline, but there is still a conflict. Instead of asking
                        # the user to merge baseline, ask them to merge their previous
                        # commit incorporating baseline, so we keep making progress.
                        to_merge = u
                    continue
                self.q.save_queuefile(commit_message=self.m)
            else:
                self.q.save_queuefile(amend=True)

            # u contains baseline, and u is merged.  Conflict is resolved.
            if contains_baseline:
                return

            # See if baseline will merge now
            try:
                self.git("merge", "--no-ff", baseline.sha, "-m", self.m)
            except GitFailed:
                if not self.git.merge_in_progress:
                    raise
                self.git("merge", "--abort")
                continue
            else:
                self.q.save_queuefile(amend=True)
                return

        if self.git.rev_parse("HEAD") != head:
            self.git.checkout(head)

        # to_merge conflicts with HEAD.  Find an appropriate (not a "merged
        # baselines") commit in HEAD which it conflicts with, and ask the
        # user to resolve the conflict.
        for commit in self.git.commits("HEAD", *(f"^{b.sha}^@" for b in self.qf.baselines)):
            if is_merged_baseline(commit):
                continue
            if self.would_conflict(to_merge, commit.sha):
                break
        else:
            raise Exception

        self.suspended_at = head
        self.git.checkout(commit.sha, comment="baseline")
        try:
            self.git("merge", "-m", "resolved conflicts", to_merge)
            raise Exception("merge succeeded, but expected failure")
        except GitFailed:
            if not self.git.merge_in_progress:
                raise
        if Queue.queuefile_name in self.git.unmerged_files():
            self.git("rm", "-f", Queue.queuefile_name)

        # suspend to allow the user to resolve the conflict
        with MergeContinue():
            raise Suspend(status="resolve conflicts and continue")


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
