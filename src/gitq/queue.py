from dataclasses import dataclass, field, replace
from typing import List, Iterator, ContextManager, Literal
from io import StringIO
from pathlib import Path
from contextlib import contextmanager
from functools import cached_property
import email.utils
import os
import re

import yaml

from .output import Output
from .git import Git, Commit, GitFailed, UserError, Sha, Ref, Branch
from .continuations import (
    EditBranch,
    Step,
    Then,
    CheckoutBranch,
    progn,
    Continuation,
    Suspend,
    Resume,
    Heading,
    handles,
    SavePatch,
    Skip,
)
from .port import port_user_merge
from .yaml import YAMLObject, BaseLoader


class Detect:
    "A sentinel value"


DETECT = Detect()


class Loader(BaseLoader):
    pass


class Dumper(yaml.Dumper):
    pass


@dataclass
class Baseline(YAMLObject):

    yaml_loader = Loader
    yaml_dumper = Dumper

    sha: Sha
    ref: str | None = field(default=None)
    remote: str | None = field(default=None)

    def _summary(self, git: Git) -> tuple[str, str]:
        commit = git.commit(self.sha)
        if not self.remote:
            if not self.ref:
                return commit.abbrev, commit.title
            try:
                ref = git.abbrev_symbolic(self.ref)
            except GitFailed:
                ref = self.ref  # ref might have been deleted
            return commit.abbrev, f"({ref}) {commit.title}"

        remote = self.remote
        if remote_name := git.find_remote(self.remote):
            remote = remote_name

        assert self.ref
        if remote_name and self.ref.startswith("refs/heads/"):
            branch = self.ref.removeprefix("refs/heads/")
            ref = f"refs/remotes/{remote_name}/{branch}"
            try:
                if git.sha(ref) == self.sha:
                    ref = git.abbrev_symbolic(ref)
                    return commit.abbrev, f"({ref}) {commit.title}"
            except GitFailed:
                pass  # remote ref could have been deleted

        return commit.abbrev, f"({self.ref} @ {remote}) {commit.title}"

    @property
    def summary(self) -> str:
        abbrev, title = self._summary(self.git)
        return f"{abbrev} {title}"

    @property
    def title(self) -> str:
        return self._summary(self.git)[1]


@dataclass
class QueueFile(YAMLObject):

    yaml_loader = Loader
    yaml_dumper = Dumper

    title: str | None = field(default=None)
    description: str | None = field(default=None)
    baselines: List[Baseline] = field(default_factory=list)
    commits: List[str] | None = field(default=None)
    unapplied_patches: List[str] | None = field(default=None)


@dataclass
class RebaseOptions(YAMLObject):

    use_local: bool = True
    to_bare: bool | None = None
    user_merges: List[Sha] = field(default_factory=list)
    force: bool = False
    onto: List[Baseline] | None = None
    refresh: bool = True

    def for_recurse(self) -> "RebaseOptions":
        return RebaseOptions(use_local=self.use_local)


yaml.add_path_resolver("!QueueFile", [], Loader=Loader, Dumper=Dumper)
yaml.add_path_resolver("!Baseline", ["baselines", None], Loader=Loader, Dumper=Dumper)


# These can appear in continuation files (e.g. as RebaseOne.onto), so register there too.
Continuation.register(Baseline)
Continuation.register(QueueFile)
Continuation.register(RebaseOptions)

CommitType = Literal["baseline", "update-queuefile", "save-patch", "apply"]


def message(m: str, type: CommitType, title: str | None = None):
    trailers = f"GitQ-Type: {type}"
    if title:
        return f"{m}: {title}\n\n{trailers}"
    else:
        return f"{m}\n\n{trailers}"


def from_this_tool(c: Commit) -> bool:
    trailers = c.trailers()
    if trailers.get("Tool") == "gitq":
        return True  # Old trailers.  TODO remove this
    return bool(trailers.get("GitQ-Type"))


def is_merged_baseline(c: Commit) -> bool:
    trailers = c.trailers()
    if trailers.get("Tool") == "gitq" and (
        c.message.startswith("baseline") or c.message.startswith("merged baseline")
    ):
        return True  # Old trailers.  TODO remove this
    return trailers.get("GitQ-Type") == "baseline"


class NotAQueue(UserError):
    pass


class Queue:

    git: Git
    qf: QueueFile
    bare: Branch | None

    def __init__(self, git: Git, *, qf: QueueFile | None = None, bare: Branch | None | Detect):
        self.git = git
        if qf:
            assert not isinstance(bare, Detect)
            self.qf = qf
            self.bare = bare
            return
        if isinstance(bare, Detect):
            assert qf is None
            if branch := self.git.branch():
                try:
                    self.qf = self.read_qf_from_config(branch, git=git)
                    self.bare = branch
                    return
                except GitFailed:
                    pass
            bare = None
        if bare is not None:
            self.qf = self.read_qf_from_config(bare, git=git)
            self.bare = bare
            return
        elif self.queuefile_path.exists():
            self.bare = None
            with open(self.queuefile_path, "r") as f:
                self.qf = yaml.load(f, Loader=Loader)
            return
        raise NotAQueue("This branch is not a queue.")

    queuefile_name = ".git-queue"

    @property
    def qf_config_name(self):
        assert self.bare
        return self.qf_config_name_for(self.bare)

    @staticmethod
    def qf_config_name_for(branch: Branch):
        assert branch
        return f"branch.{branch}.git-queue"

    @staticmethod
    def historiography_config_name(branch: Branch):
        return f"branch.{branch}.gitq-historiography"

    @property
    def queuefile_path(self) -> Path:
        return self.git.directory / self.queuefile_name

    @classmethod
    def read_qf_from_config(cls, branch: Branch, *, git: Git):
        y = git("config", "get", cls.qf_config_name_for(branch), quiet=True)
        return yaml.load(StringIO(y), Loader=Loader)

    @classmethod
    def is_queue(cls, git: Git) -> bool:
        "check if HEAD is a queue"
        if branch := git.branch():
            try:
                cls.read_qf_from_config(branch, git=git)
                return True
            except GitFailed:
                pass
        return (git.directory / cls.queuefile_name).exists()

    @classmethod
    def qf_for_ref(cls, ref: Ref, *, git: Git) -> QueueFile | None:
        if (branch := ref.removeprefix("refs/heads/")) != ref:
            try:
                return cls.read_qf_from_config(branch, git=git)
            except GitFailed:
                pass
        try:
            content = git("show", f"{ref}:{cls.queuefile_name}", quiet=True)
            return yaml.load(StringIO(content), Loader=Loader)
        except GitFailed:
            pass
        return None

    def save_queuefile(
        self,
        qf: QueueFile | None = None,
        *,
        amend: bool = False,
        commit_message: str = "",
        stage: bool | None = None,
    ):
        if qf is None:
            qf = self.qf
        assert amend + (stage is not None) + bool(commit_message) == 1 or (
            self.bare and not commit_message
        )
        if self.bare:
            with StringIO() as f:
                yaml.dump(qf, f, Dumper=Dumper)
                y = f.getvalue()
            self.git("config", "set", self.qf_config_name, y, quiet=True)
        else:
            with open(self.queuefile_path, "w") as f:
                yaml.dump(qf, f, Dumper=Dumper)
            if stage or amend or commit_message:
                self.git("add", self.queuefile_path.relative_to(self.git.directory))
            if amend:
                self.git("commit", "--amend", "--allow-empty", "-C", "HEAD")
        if commit_message:
            self.git("commit", "--allow-empty", "-m", commit_message)

    def init(self, *, branch: str = ""):
        if branch:
            self.git.detach()
            if self.bare:
                self.save_queuefile()
            else:
                self.save_queuefile(commit_message="new queue branch")
            progn(
                MergeBaselines(
                    RebaseOptions(), old_baselines=self.qf.baselines, qf=self.qf, bare=self.bare
                ),
                NewBranch(branch),
            )
        else:
            if self.is_queue(self.git):
                raise UserError("Already a queue, cannot init.")
            self.save_queuefile(stage=True)
            if self.bare:
                return
            self.git(
                "commit",
                "--allow-empty",
                "-m",
                message("initialized queue", "update-queuefile", self.qf.title),
            )

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

    def get_commits(self, reverse: bool = False) -> List[Commit]:
        return self.git.commits(*(f"^{b.sha}" for b in self.qf.baselines), "HEAD", reverse=reverse)

    def queue_is_clean(self) -> bool:
        """
        Check if queue history is clean -- that is, it only contains
        merged baseline commits and patches
        """
        baseline_shas: set[str] = set()
        for c in self.get_commits():
            if is_merged_baseline(c):
                baseline_shas.add(c.sha)
            if c.sha in baseline_shas:
                for p in c.parents:
                    baseline_shas.add(p)
                continue
            if c.is_merge or from_this_tool(c):
                return False
        return True

    @classmethod
    def find_patches(
        cls, ref: str, baselines: List[Baseline], new_base: str, *, git: Git
    ) -> Iterator[Commit]:
        if git.on_orphan_branch():
            return
        commits = git.commits(*(f"^{b.sha}" for b in baselines), ref, reverse=True)
        user_merges = {c.sha for c in cls.find_user_merges(list(reversed(commits)))}
        base = cls.find_git_cherry_limit(commits, git=git)
        # We use the + side instead of the - side of the `git cherry`
        # output to detect duplicates, because if we used the - side, then
        # it would only filter out distinct (different sha) commits that
        # are duplicated, but it does not filter out commits that are
        # literally present (same sha) in both branch and new_base.
        new = set(r.sha for r in git.find_duplicates(base, ref, new_base) if r.is_new)
        for commit in commits:
            if commit.sha in user_merges:
                continue
            if from_this_tool(commit):
                continue
            if commit.is_merge:
                if git.is_conflicted(commit):
                    raise UserError(f"rebasing merges is not implemented yet: {commit.summary}")
                continue
            if commit.sha not in new:
                continue
            changed = git("show", "--name-only", "--pretty=", commit.sha, quiet=True).strip()
            if changed == cls.queuefile_name:
                continue
            yield commit

    def baselines_for_swap(self) -> Iterator[Sha]:
        "return a list of shas that git-swap should not proceed past"
        for b in self.qf.baselines:
            yield b.sha
        for commit in self.get_commits():
            if is_merged_baseline(commit):
                yield commit.sha

    @classmethod
    def find_git_cherry_limit(cls, commits: List[Commit], git: Git) -> str | None:
        "Find the 'baseline' or 'merged baselines' commit in the queue"
        merges = [c.sha for c in commits if is_merged_baseline(c)]
        if len(merges) == 0:
            # See below, just pick some limit.  Can't return them all
            if commits and commits[0].parents:
                return commits[0].parents[0]
            return None
        bases = git("merge-base", "--independent", *merges, quiet=True).strip().splitlines()
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

    def rebase(
        self,
        opts: RebaseOptions,
    ) -> None:
        if self.is_historiography:
            raise UserError("Cannot rebase a historiography")
        with Heading("Rebasing queue"):
            Rebase(
                opts,
                bare=self.bare,
            ).run()

    # TODO if baseline is a remote branch, but there is a local branch
    # tracking it, detect that.

    @classmethod
    def needs_rebase(
        cls, ref: Ref | None, seen: frozenset[str] = frozenset(), *, git: Git, opts: RebaseOptions
    ) -> bool:
        "Return True if the local queue branch at ref has baselines that have been updated."
        if ref is None or not ref.startswith("refs/heads/"):
            return False
        if ref in seen:
            return False
        qf = cls.qf_for_ref(ref, git=git)
        if qf is None:
            return False
        seen = seen | {ref}
        for b in qf.baselines:
            if refresh_baseline(b, git=git, opts=opts).sha != b.sha:
                return True
            if cls.needs_rebase(b.ref, seen, git=git, opts=opts):
                return True
        return False

    @property
    def commits_directory(self) -> Path:
        return self.git.directory / "commits"

    @property
    def patches_directory(self) -> Path:
        return self.git.directory / "patches"

    def write_commits(self) -> Iterator[Path]:
        os.makedirs(self.commits_directory, exist_ok=True)
        commits = self.get_commits(reverse=True)
        num_digits = len(str(len(commits) - 1))
        for i, commit in enumerate(commits):
            yield commit.make_patch_file(self.commits_directory, index=(i, num_digits))

    @property
    def is_historiography(self) -> bool:
        return self.qf.commits is not None

    @classmethod
    def set_historiography_branch(cls, of: Branch, to: Branch, *, git: Git):
        config_name = cls.historiography_config_name(of)
        git("config", "set", config_name, f"refs/heads/{to}")

    @classmethod
    def get_historiography_branch(cls, of: Branch, *, git: Git) -> Branch | None:
        try:
            config_name = cls.historiography_config_name(of)
            cfg = git("config", config_name, quiet=True).strip()
            if not cfg.startswith("refs/heads/"):
                raise Exception(f"Not a branch: {cfg}")
            return cfg
        except GitFailed:
            return None

    def historiography_branch(self) -> Branch:
        branch = self.git.branch()
        if branch is None:
            raise UserError("HEAD is not a branch")
        if hb := self.get_historiography_branch(branch, git=self.git):
            return hb
        config_name = self.historiography_config_name(branch)
        raise UserError(f"Commit to where?  Set config {config_name}")

    @classmethod
    def find_queue_branch(cls, branch: Branch, *, git: Git) -> Branch | None:
        "Find the queue branch whose historiography config points to historio_ref."
        try:
            output = git.cmd(
                ["git", "config", "--get-regexp", r"branch\..*\.gitq-historiography"],
                quiet=True,
            ).strip()
        except GitFailed:
            return None
        for line in output.splitlines():
            key, value = line.split(" ", 1)
            if value == "refs/heads/" + branch:
                return key.removeprefix("branch.").removesuffix(".gitq-historiography")
        return None

    def commit(self, *, message: str = "", meta_branch: str):
        sha = self.git.sha("HEAD")

        with self.git.temp_index_and_files():
            qf = replace(self.qf)
            qf.commits = list()
            for patch in self.write_commits():
                self.git("add", patch)
                qf.commits.append(str(patch.relative_to(self.git.directory)))
            self.save_queuefile(qf, stage=True)
            tree = Sha(self.git("write-tree").strip())

            with Heading("Checking round-trip"):
                if Queue(self.git, qf=qf, bare=None).recreate_queue() != sha:
                    raise Exception("re-created queue does not match original")

        with CheckoutBranch(meta_branch, orphan=not self.git.ref_exists(meta_branch)):
            self.git("read-tree", tree)
            if message:
                self.git.cmd(["git", "commit", "-m", message])
            else:
                self.git.cmd(["git", "commit"], interactive=True)

    def apply_patch(self, patch: str | None) -> None:
        if not self.qf.unapplied_patches:
            raise UserError("No unapplied patches")

        if patch:
            patch = os.path.relpath(patch, self.git.directory)
        else:
            patch = self.qf.unapplied_patches[0]

        if not (self.git.directory / patch).exists():
            raise UserError(f"Patch file not found: {patch}")

        with ApplyPatchContinue(patch, bare=self.bare):
            try:
                self.git.cmd(["git", "am", "--3way", patch])
            except GitFailed:
                if (self.git.gitdir / "rebase-apply").exists():
                    raise Suspend(status="Resolve the conflicts.")
                raise

    def recreate_queue(self) -> Sha:
        if not self.qf.commits:
            raise UserError("This is not a historiography branch")
        with self.git.temp_index() as git:
            for path in self.qf.commits:
                sha = self.recreate_commit(path, git=git)
        return sha  # type: ignore[possibly-undefined]

    def recreate_commit(self, patch_path: str, *, git: Git) -> Sha:
        with open(git.directory / patch_path, "r") as f:
            msg = email.message_from_file(f)
        committer_name, committer_email = email.utils.parseaddr(msg["GitQ-Committer"])
        committer_date = msg["GitQ-CommitterDate"]
        author_name, author_email = email.utils.parseaddr(msg["From"])
        author_date = msg["Date"]
        parents = [Sha(x) for x in msg["GitQ-Parents"].split()]
        title = msg["Subject"].removeprefix("[PATCH] ")
        body_raw = msg.get_payload()
        m = re.match(r"From ([0-9a-fA-F]+)", msg.get_unixfrom() or "")
        if not parents or not isinstance(body_raw, str) or not m:
            raise ValueError(f"bad patch: {patch_path}")
        sha0 = Sha(m.group(1))
        sep = re.search(r"\n---\n|\ndiff ", body_raw)
        body = body_raw[: sep.start()].strip() if sep else body_raw.strip()
        commit_message = title + "\n" if not body else title + "\n\n" + body + "\n"

        git.env.update(
            {
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_AUTHOR_DATE": author_date,
                "GIT_COMMITTER_NAME": committer_name,
                "GIT_COMMITTER_EMAIL": committer_email,
                "GIT_COMMITTER_DATE": committer_date,
            }
        )

        git.cmd(["git", "read-tree", parents[0]], quiet=True)
        git.cmd(["git", "apply", "--cached", patch_path], quiet=True)
        tree = Sha(git.cmd(["git", "write-tree"], quiet=True).strip())

        def p():
            for parent in parents:
                yield "-p"
                yield parent

        cmd = ["git", "commit-tree", tree, *p(), "-m", commit_message]
        sha = Sha(git.cmd(cmd, quiet=True).strip())

        if sha0 != sha:
            raise Exception("re-created sha does not match")

        return sha


@dataclass
class RebaseBranch(Step):
    "Temporarily checkout a the specified branch and rebase it."

    ref: Ref
    opts: RebaseOptions

    def run(self):
        with Output.heading(f"rebasing branch {self.ref}"), CheckoutBranch(self.ref):
            q = Queue(self.git, bare=DETECT)
            Rebase(self.opts, bare=q.bare).run()


@contextmanager
def nop_context():
    yield


@dataclass
class RestoreConfig(Continuation):

    qf: QueueFile
    branch: Branch

    @classmethod
    def from_q(cls, q: Queue) -> ContextManager:
        if q.bare:
            return cls(q.qf, q.bare)
        return nop_context()

    @contextmanager
    def impl(self) -> Iterator:
        try:
            yield
        except (Exception, Resume):
            Queue(self.git, qf=self.qf, bare=self.branch).save_queuefile()
            raise


@dataclass
class RebaseOne(Step):
    "Rebase a single branch (not recursive)."

    opts: RebaseOptions
    bare: Branch | None
    onto: List[Baseline] | None = None

    def run(self):
        old_q = Queue(self.git, bare=self.bare)
        q = Queue(self.git, bare=self.bare)

        if self.onto is None:
            if self.opts.onto is None:
                self.onto = q.qf.baselines
            else:
                self.onto = self.opts.onto

        if self.opts.refresh:
            q.qf.baselines = [refresh_baseline(b, git=self.git, opts=self.opts) for b in self.onto]
        else:
            q.qf.baselines = list(self.onto)

        if self.opts.to_bare and (branch := self.git.branch()):
            q.bare = branch
            q.save_queuefile()
        if self.opts.to_bare is False:
            if q.bare:
                self.git("config", "unset", q.qf_config_name)
            q.bare = None

        old_sha = self.git.sha("HEAD") if not self.opts.refresh else None

        with RestoreConfig.from_q(old_q), EditBranch(message="git-queue rebase") as head:
            progn(
                MergeBaselines(
                    self.opts,
                    old_baselines=old_q.qf.baselines,
                    qf=q.qf,
                    bare=q.bare,
                    old_head=self.git.sha(head),
                ),
                FindAndPickCherries(head, old_q.qf.baselines, self.bare),
            )

        if old_sha:
            diff = self.git.cmd(["git", "diff", "--name-only", old_sha, "HEAD"]).strip()
            if diff:
                Output.print(f"Warning: content changed!\n{diff}\n")


@dataclass
class QueueCherryPickContinue(Continuation):
    "Like CherryPickContinue, but on SavePatch: save the commit, update the queue, and commit."

    ref: Sha
    bare: Branch | None

    @contextmanager
    def impl(self) -> Iterator:
        try:
            with handles(Skip, SavePatch):
                yield
        except Skip:
            self.git.cherry_pick_abort()
            return
        except SavePatch:
            q = Queue(self.git, bare=self.bare)
            self.git.cherry_pick_abort()
            commit = self.git.commit(self.ref)
            os.makedirs(q.patches_directory, exist_ok=True)
            existing = list(q.qf.unapplied_patches or [])
            patch_path = commit.make_patch_file(q.patches_directory, index=(len(existing), 1))
            rel = str(patch_path.relative_to(self.git.directory))
            self.git("add", rel)
            q.qf.unapplied_patches = existing + [rel]
            q.save_queuefile(commit_message=message("save patch", "save-patch"))
            return
        except (Exception, Resume):
            self.git.cherry_pick_abort()
            raise
        if self.git.cherry_pick_in_progress:
            if self.git.has_unmerged_files():
                Output.print("The index still has unmerged files.")
                raise Suspend(status="Resolve the conflicts.")
            self.git.cmd(["git", "cherry-pick", "--continue"])


@dataclass
class QueuePickCherries(Continuation):
    "Like PickCherries, but uses QueueCherryPickContinue for save-patch support."

    cherries: List[Sha]
    bare: Branch | None
    edit: bool = field(default=False)

    @contextmanager
    def impl(self) -> Iterator:
        yield
        while self.cherries:
            cherry, *self.cherries = self.cherries
            commit = self.git.commit(cherry)
            with Heading(f"Cherry picking {commit.summary}", quiet=True):
                try:
                    self.git.cmd(
                        ["git", "cherry-pick", "--quiet", "--allow-empty", cherry],
                        comment=commit.title,
                    )
                except GitFailed:
                    if self.edit and self.git.cherry_pick_in_progress:
                        with QueueCherryPickContinue(ref=cherry, bare=self.bare):
                            raise Suspend(status="Resolve the conflicts.")
                    else:
                        self.git.cherry_pick_abort()
                        raise


@dataclass
class ApplyPatchContinue(Continuation):
    "After a conflicting git am, finish applying the patch on resume."

    patch: str
    bare: Branch | None

    @contextmanager
    def impl(self) -> Iterator:
        try:
            with handles(Skip):
                yield
        except Skip:
            self.git.cmd(["git", "am", "--abort"])
            return
        except (Exception, Resume):
            self.git.cmd(["git", "am", "--abort"])
            raise

        if (self.git.gitdir / "rebase-apply").exists():
            if self.git.has_unmerged_files():
                Output.print("The index still has unmerged files.")
                raise Suspend(status="Resolve the conflicts.")
            self.git.cmd(["git", "am", "--continue"])

        title = self.git.commit("HEAD").title
        self.git.cmd(["git", "rm", "--quiet", self.patch])
        q = Queue(self.git, bare=self.bare)
        patches = list(q.qf.unapplied_patches or [])
        patches.remove(self.patch)
        q.qf.unapplied_patches = patches or None
        q.save_queuefile(commit_message=message("apply patch", "apply", title))


@dataclass
class FindAndPickCherries(Step):
    "Find patches in head, and then apply them to HEAD"

    head: Ref | Sha
    old_baselines: List[Baseline]
    bare: Branch | None

    def run(self) -> None:
        patches = list(Queue.find_patches(self.head, self.old_baselines, "HEAD", git=self.git))
        with QueuePickCherries(cherries=[b.sha for b in patches], bare=self.bare, edit=True):
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

    opts: RebaseOptions
    bare: Branch | None

    def run(self) -> None:
        steps: List[Step] = list()

        q = Queue(self.git, bare=self.bare)
        if self.opts.refresh:
            for baseline in q.qf.baselines:
                if baseline.remote:
                    if self.opts.use_local:
                        branch = self.git.find_local(baseline.ref, baseline.remote)
                        if branch == self.git.head():
                            continue  # can happen if current branch is set to track a baseline
                        if branch and q.needs_rebase(branch, git=self.git, opts=self.opts):
                            steps.append(RebaseBranch(branch, self.opts.for_recurse()))
                    continue
                if q.needs_rebase(baseline.ref, git=self.git, opts=self.opts):
                    assert baseline.ref
                    steps.append(RebaseBranch(baseline.ref, opts=self.opts.for_recurse()))

        if (
            not self.opts.force
            and not steps
            and all(
                refresh_baseline(b, git=self.git, opts=self.opts).sha == b.sha
                for b in q.qf.baselines
            )
            and all(self.git.is_ancestor(b.sha) for b in q.qf.baselines)
            and q.queue_is_clean()
            and (self.opts.to_bare is None or (bool(self.bare) == self.opts.to_bare))
        ):
            Output.print("Already up to date.")
            return

        steps.append(RebaseOne(self.opts, bare=self.bare))

        with Then(steps=steps):
            pass


@dataclass
class MergeContinue(Continuation):
    """
    When resuming, check if the user ran `git commit`, and do it for them
    if they haven't.
    """

    head: Sha
    to_merge: Sha

    @property
    def status(self) -> str:
        return (
            f"Merging {self.git.commit(self.head).summary}\n"
            + f"   with {self.git.commit(self.to_merge).summary}\n"
            + "Resolve the conflicts."
        )

    @contextmanager
    def impl(self) -> Iterator["MergeContinue"]:
        try:
            yield self
        except (Exception, Resume):
            self.git("merge", "--abort")
            raise
        if self.git.merge_in_progress:
            if self.git.has_unmerged_files():
                Output.print("The index still has unmerged files.")
                raise Suspend(status=self.status)
            self.git("commit", "--no-edit")


@dataclass
class MergeBaselines(Step, Continuation):

    opts: RebaseOptions
    old_baselines: List[Baseline]
    qf: QueueFile
    bare: Branch | None
    user_merges: List[Sha] = field(default_factory=list)
    find_user_merges: bool = True
    needs_checkout: bool = True
    suspended_at: Sha | None = None
    old_head: Sha | None = field(default=None)

    def run(self) -> None:
        with self:
            pass

    @contextmanager
    def impl(self) -> Iterator:
        yield
        with Heading("Merging baselines"):
            self.check_user_merges()
            if self.suspended_at:
                # If continued after asking the user to make a merge, pick it
                # up and add it to the list of user merges, and go back to the
                # commit we were at before.
                self.user_merges.append(self.git.sha("HEAD"))
                self.git.checkout(self.suspended_at, comment="go back")
                self.suspended_at = None
            self.merge_baselines()

    @cached_property
    def q(self):
        return Queue(self.git, qf=self.qf, bare=self.bare)

    def save_queuefile(
        self,
        amend: bool = False,
        commit_message: str = "",
    ) -> None:
        for patch_name in self.qf.unapplied_patches or ():
            assert self.old_head
            self.git("checkout", self.old_head, "--", patch_name)
        if amend or commit_message:
            self.q.save_queuefile(amend=amend, commit_message=commit_message)
        elif self.qf.unapplied_patches:
            self.q.save_queuefile(commit_message=message("save patches", "save-patch"))
        else:
            self.q.save_queuefile()

    def still_needed(self) -> Iterator[Baseline]:
        "return a list of baselines that have not yet been merged"
        for baseline in self.qf.baselines:
            if not self.git.is_ancestor(baseline.sha):
                yield baseline

    @cached_property
    def m(self) -> str:
        return message("merged baselines", "baseline", self.qf.title)

    def port_user_merge(self, M: Commit) -> Commit | None:
        B = [b.sha for b in self.old_baselines]
        Bʹ = [b.sha for b in self.qf.baselines]
        with Heading("porting user merge " + M.summary):
            return port_user_merge(M, B, Bʹ, git=self.git)

    def check_user_merges(self):
        """
        Find user merges in HEAD.  Ensure that all user merges are clean,
        that is they do not introduce any new commits outside of the
        baselines.
        """
        if not self.find_user_merges:
            return
        self.find_user_merges = False
        self.user_merges.extend(self.opts.user_merges)

        # Find user merges in HEAD
        # This is the OLD queue, before baselines have been updated
        commits = self.git.commits("HEAD", *(f"^{b.sha}" for b in self.old_baselines))
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
                U = self.git.commit(u)
                if U2 := self.port_user_merge(U):
                    Output.print(f"Ported user merge {U.abbrev} -> {U2.abbrev}.")
                    clean.append(U2.sha)
                else:
                    Output.print(f"User merge {U.abbrev} is not clean and could not be ported.")
        self.user_merges = clean

    def try_octopus(self, *shas: Sha) -> bool:
        try:
            self.git("merge", "--no-ff", *shas, "-m", self.m)
        except GitFailed:
            if self.git.merge_in_progress:
                if self.git.unmerged_files() == {Queue.queuefile_name}:
                    self.save_queuefile(commit_message=self.m)
                    return True
                self.git("merge", "--abort")
        else:
            self.save_queuefile(amend=True)
            return True
        return False

    def merge_baselines(self) -> None:
        q = self.q

        if len(self.user_merges) > 1:
            self.user_merges = self.git.independent(*self.user_merges)

        # First, check out one of the baselines so there's something to
        # merge into
        if self.needs_checkout:
            if not self.qf.baselines:
                raise UserError("Cannot rebase queue onto zero baselines.")
            b0 = self.qf.baselines[0]
            self.git.checkout(b0.sha, comment=b0.title)
            self.needs_checkout = False

        needed = list(self.still_needed())
        if not needed:
            if not q.bare:
                self.save_queuefile(commit_message=message("baseline", "baseline", q.qf.title))
            else:
                self.save_queuefile()
            return

        # See if octopus merge can do it.
        if self.try_octopus(*(b.sha for b in needed)):
            return
        if self.user_merges and self.try_octopus(*(b.sha for b in needed), *self.user_merges):
            return

        # Merge one at a time
        while needed:
            baseline = needed.pop(0)
            with Heading(f"Merging {baseline.summary}"):
                try:
                    self.git(
                        "merge", "--no-ff", baseline.sha, "-m", self.m, comment=baseline.title
                    )
                except GitFailed:
                    if not self.git.merge_in_progress:
                        raise
                    if self.git.unmerged_files() == {q.queuefile_name}:
                        self.save_queuefile(commit_message=self.m)
                        continue
                    self.git("merge", "--abort")
                else:
                    self.save_queuefile(amend=True)
                    continue
                # Oh, no!  A conflict!
                self.resolve_conflicts(baseline)
                needed = list(self.still_needed())

    def would_conflict(self, a: str, b: str) -> bool:
        _, conflicts = self.git.merge_tree(a, b)
        return not (conflicts <= {Queue.queuefile_name})

    def resolve_conflicts(self, baseline: Baseline):

        head = self.git.sha("HEAD")
        to_merge = baseline.sha

        # Try to find a user merge that can resolve the conflict
        for u in self.user_merges:
            contains_baseline = self.git.is_ancestor(baseline.sha, of=u)
            if self.git.sha("HEAD") != head:
                self.git.checkout(head)

            # try to merge u
            try:
                self.git("merge", "--no-ff", u, "-m", self.m, comment=self.git.commit(u).title)
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
                self.save_queuefile(commit_message=self.m)
            else:
                self.save_queuefile(amend=True)

            # u contains baseline, and u is merged.  Conflict is resolved.
            if contains_baseline:
                return

            # See if baseline will merge now
            try:
                self.git("merge", "--no-ff", baseline.sha, "-m", self.m, comment=baseline.title)
            except GitFailed:
                if not self.git.merge_in_progress:
                    raise
                self.git("merge", "--abort")
                continue
            else:
                self.save_queuefile(amend=True)
                return

        # to_merge conflicts with HEAD.  Find an appropriate (not a "merged
        # baselines") commit in HEAD which it conflicts with, and ask the
        # user to resolve the conflict.
        for commit in self.git.commits(head, *(f"^{b.sha}^@" for b in self.qf.baselines)):
            if is_merged_baseline(commit):
                continue
            if self.would_conflict(to_merge, commit.sha):
                break
        else:
            raise Exception

        self.suspended_at = head
        self.git.checkout(commit.sha, comment=commit.title)
        try:
            self.git("merge", "-m", "resolved conflicts", to_merge)
            raise Exception("merge succeeded, but expected failure")
        except GitFailed:
            if not self.git.merge_in_progress:
                raise
        if Queue.queuefile_name in self.git.unmerged_files():
            self.git("rm", "-f", Queue.queuefile_name)

        # suspend to allow the user to resolve the conflict
        with MergeContinue(head, to_merge) as m:
            raise Suspend(status=m.status)


def refresh_baseline(baseline: Baseline, *, git: Git, opts: RebaseOptions) -> Baseline:
    if baseline.ref is None:
        return baseline
    elif baseline.remote:
        if opts.use_local and (local := git.find_local(baseline.ref, baseline.remote)):
            fetched = local
        elif baseline.ref.startswith("refs/heads/") and (name := git.find_remote(baseline.remote)):
            git.fetch(name)
            branch = baseline.ref.removeprefix("refs/heads/")
            fetched = f"refs/remotes/{name}/{branch}"
        else:
            git.cmd(["git", "fetch", baseline.remote, baseline.ref])
            fetched = "FETCH_HEAD"
        return Baseline(git.commit(fetched).sha, baseline.ref, baseline.remote)
    else:
        return Baseline(git.commit(baseline.ref).sha, baseline.ref, None)
