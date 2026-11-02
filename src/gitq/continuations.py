import re
import sys
import yaml
from typing import (
    Optional,
    List,
    TypeVar,
    ContextManager,
    Generic,
    Iterator,
    NoReturn,
    Sequence,
    Type,
    Tuple,
)
from contextvars import ContextVar
from contextlib import contextmanager
from itertools import count
from abc import abstractmethod
from dataclasses import dataclass, field

from .output import Output
from .git import (
    Git,
    UserError,
    GitFailed,
    MergeFound,
    Commit,
    Sha,
    Ref,
    Branch,
    GITQ_TYPE,
    add_trailer,
    strip_trailer,
)
from .yaml import YAMLObject, BaseLoader, contextGit


class Loader(BaseLoader):
    "YAML loader for .git/continuation.yaml"


class Dumper(yaml.Dumper):
    "YAML dumper for .git/continuation.yaml"


@dataclass
class Continuations(YAMLObject):
    "Root object for .git/continuation.yaml"

    yaml_loader = Loader
    yaml_dumper = Dumper

    continuations: List["Continuation"]
    tool: str
    ok_resumes: List[str]
    continue_command: str | None = None
    status: str | None = field(default=None)


yaml.add_path_resolver("!Continuations", [], Loader=Loader, Dumper=Dumper)

T = TypeVar("T")


ok_resumes: ContextVar[Tuple[type, ...]] = ContextVar("ok_resumes", default=())


@contextmanager
def handles(*types: type):
    token = ok_resumes.set(ok_resumes.get() + types)
    try:
        yield
    finally:
        ok_resumes.reset(token)


class Suspend(BaseException):
    "Suspend execution and save a stack of continuations in .git/continuation.yaml"

    continuations: List["Continuation"]
    status: Optional[str]
    ok_resumes: list[str]

    def __init__(self, *, status: str | None = None) -> None:
        super().__init__()
        self.status = status
        self.continuations = list()
        self.ok_resumes = [t.__name__ for t in ok_resumes.get()]


class Resume(BaseException):
    "Resume execution with some additional instruction from the user."


class Abort(Exception):
    """
    Raised into a resume stack by `--abort`.  This will abort the operation
    and restore git to its previous state.
    """


class Continuation(Generic[T], YAMLObject):
    """
    A continuation is  is a context manager that can be suspended,
    serialized out to yaml, and then resumed in a subsequent execution of
    this program.

    This is a very low-tech approach to serializeable continuations, and it
    relies on suspendable code being written in a strange idiom to work.

    Anything that needs to happen after a resume needs to be expressed as a
    stack of `Continuation` instances, rather than ordinary function calls.

    A continuation class must:

      * be a dataclass with only serializable attributes

      * implement a context manager overriding `.impl()`

      * be prepared to reconstruct the execution state of `.impl()`, if it
        calls anything that might raise Suspend.   In other words, there is
        no magic here that somehow serializes the python execution state.
        Each `Continuation` instance is just going to be reanimated based
        on its serializeable attributes, and resume again from the yield.
    """

    yaml_loader = Loader
    yaml_dumper = Dumper

    manager: ContextManager[T] = field(metadata={"yaml_exclude": True})

    def __enter__(self) -> T:
        self.manager = self.impl()
        try:
            return self.manager.__enter__()
        except Suspend as exception:
            raise Exception("continuations must not suspend before yield") from exception

    def __exit__(self, exception_type, exception, traceback) -> bool | None:
        if exception is None and exception_type is not None:
            exception = exception_type()

        if isinstance(exception, Suspend):
            exception.continuations.append(self)
            self.manager.__exit__(exception_type, exception, traceback)
            return None

        try:
            return self.manager.__exit__(exception_type, exception, traceback)
        except Suspend as exception:
            exception.continuations.append(self)
            raise

    @abstractmethod
    def impl(self) -> ContextManager[T]:
        pass

    @staticmethod
    def register(c: Type[YAMLObject]) -> None:
        "register a class for yaml serialization in continuations"
        Loader.add_constructor(c.yaml_tag, c.from_yaml)
        Dumper.add_representer(c, c.to_yaml)


class Main:

    tool: str
    continue_command: str

    @abstractmethod
    def main(self) -> None:
        pass

    def __call__(self) -> NoReturn:
        self.git = Git()
        contextGit.set(self.git)
        try:
            self.main()
        except UserError as e:
            Output.print(e)
            sys.exit(1)
        except Abort:
            Output.print("Cancelled.  Previous state restored.")
        sys.exit(0)

    def check_clean(self):
        if not self.git.is_clean():
            raise UserError("Error: repo not clean")

    @contextmanager
    def setup(self) -> Iterator:
        self.check_clean()
        if self.git.continuation.exists():
            with open(self.git.continuation, "r") as f:
                j: Continuations = yaml.load(f, Loader)
            raise UserError(f"{j.tool} operation is already in progress.")
        try:
            yield
        except Suspend as e:
            self.suspend(e)
        except Resume as e:
            raise Exception("Internal error.  Uncaught Resume") from e

    def suspend(self, e: Suspend) -> NoReturn:
        with open(self.git.continuation, "w") as f:
            continuations = list(reversed(e.continuations))
            j = Continuations(
                continuations, self.tool, e.ok_resumes, self.continue_command, e.status
            )
            yaml.dump(j, f, Dumper=Dumper)
        self.print_conflicts()
        Output.print()
        Output.print("Suspended!")
        if e.status:
            Output.print(e.status)
        if j.continue_command:
            Output.print(f"Then continue with `{j.continue_command}`")
        sys.exit(2)

    def reanimate(self, continuations: List[Continuation], *, throw: BaseException | None) -> None:
        if not len(continuations):
            if throw is not None:
                raise throw
            else:
                return
        continuation, *continuations = continuations
        with continuation:
            self.reanimate(continuations, throw=throw)

    def resume(self, throw: BaseException | None = None) -> NoReturn:

        if not self.git.continuation.exists():
            raise UserError(f"Error: no {self.tool} operation is in progress")

        with open(self.git.continuation, "r") as f:
            j: Continuations = yaml.load(f, Loader)

        if isinstance(throw, Resume) and type(throw).__name__ not in j.ok_resumes:
            raise UserError("That type of resume is not allowed here")

        self.git.continuation.unlink()

        try:
            self.reanimate(j.continuations, throw=throw)
        except Suspend as e:
            self.suspend(e)
        except Resume as e:
            raise Exception("Internal error.  Uncaught Resume") from e

        sys.exit(0)

    def print_conflicts(self) -> None:
        conflicts = self.git.conflicts()
        if not conflicts:
            return
        width = max(len(desc) for desc, _ in conflicts)
        Output.print()
        Output.print("Unmerged paths:")
        for desc, path in conflicts:
            Output.print(f"  {desc + ':':<{width + 3}}{path}")

    def status(self) -> None:
        if not self.git.continuation.exists():
            Output.print("no operation in progress")
            self.print_conflicts()
            return
        with open(self.git.continuation, "r") as f:
            j: Continuations = yaml.load(f, Loader)

        Output.print(f"A {j.tool} operation is in progress.")
        Output.print()
        for c in j.continuations:
            if isinstance(c, Heading):
                Output.print(f" * {c.message}")
        self.print_conflicts()
        Output.print()
        Output.print(j.status)
        if j.continue_command:
            Output.print(f"Then continue with `{j.continue_command}`")


class Finally(Continuation):
    "This should be used instead of try/finally for continuation classes."

    @abstractmethod
    def cleanup(self) -> None:
        pass

    @contextmanager
    def impl(self) -> Iterator:
        try:
            yield
        except GeneratorExit:
            raise
        except (Exception, Resume):
            self.cleanup()
            raise
        except Suspend:
            raise
        except BaseException as e:
            self.cleanup()
            raise Exception(f"Unexpected BaseException: {repr(e)}")
        else:
            self.cleanup()


@dataclass
class DeleteTempBranch(Finally):

    branch: str
    previous_head: str

    def cleanup(self) -> None:
        if self.git.on_orphan_branch():
            Output.print(f"# reset back to before creating {self.branch} branch")
            self.git.force_checkout(self.previous_head)
        else:
            self.git.detach()
        if self.git.branch_exists(self.branch):
            self.git.cmd(["git", "branch", "-qD", self.branch])


@contextmanager
def temp_branch(git: Git) -> Iterator[str]:
    """
    Create a temporary branch with no content and no parents.
    """
    branches = set(git.branches())
    for n in count():
        branch = f"temp-{n}"
        if branch not in branches:
            break
    else:
        raise AssertionError

    with DeleteTempBranch(branch=branch, previous_head=git.head()):
        git.cmd(["git", "checkout", "-q", "--orphan", branch])
        git.delete_index_and_files()
        yield branch


@contextmanager
def checkout_baseline(sha: Sha | None, *, git: Git):
    """
    Checkout a baseline commit, or if argument is None, create a temporary
    branch with no history and check that out.
    """
    if sha is None:
        with temp_branch(git):
            yield
    else:
        git.checkout(sha)
        yield


@dataclass
class EditBranch(Continuation[Ref | Sha]):
    """
    Detach from the current branch, so it can be edited without polluting
    the reflog with a bunch of intermediate steps.   At the end, update the
    branch using message, and check it back out again.
    """

    message: str
    head: Ref | Sha | None = field(default=None)

    @property
    def branch(self) -> Optional[str]:
        if self.head and self.head.startswith("refs/heads/"):
            return self.head.removeprefix("refs/heads/") or None
        return None

    @contextmanager
    def impl(self) -> Iterator[str]:
        if self.head is None:
            self.head = self.git.head()
            self.git.detach()
        try:
            yield self.head
        except (Exception, Resume):
            Output.print("# Failed.  Resetting to original HEAD")
            self.git.force_checkout(self.branch or self.head)
            raise
        else:
            if self.branch:
                self.git.cmd(["git", "update-ref", "-m", self.message, self.head, "HEAD"])
                self.git.checkout(self.branch, comment="done editing branch")


@dataclass
class CheckoutBranch(Finally, Continuation[Branch]):
    "Temporarily checkout ref, then restore to previous HEAD"

    ref: Ref
    old_ref: Ref | None = field(default=None)
    orphan: bool = field(default=False)

    def cleanup(self):
        if self.old_ref is not None:
            if (branch := self.old_ref.removeprefix("refs/heads/")) != self.ref:
                self.git.force_checkout(branch, comment="restore previous branch")
            else:
                self.git.force_checkout(self.old_ref, comment="restore previous HEAD")

    @contextmanager
    def impl(self) -> Iterator[Branch]:
        branch = self.ref.removeprefix("refs/heads/")
        assert branch != self.ref
        if self.old_ref is None:
            self.old_ref = self.git.head()
            self.git.checkout(
                branch,
                comment="checkout branch",
                orphan=self.orphan,
            )
        with super().impl():
            yield branch


@dataclass
class PickCherries(Continuation):
    "Yield, then cherry-pick specified commits."

    cherries: List[Sha]
    edit: bool = field(default=False)

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        while self.cherries:
            cherry, *self.cherries = self.cherries
            commit = self.git.commit(cherry)
            if is_defer(commit):
                carry_tree_forward(commit, git=self.git)
            else:
                cherry_pick(commit, edit=self.edit, git=self.git)


class Skip(Resume):
    """
    Raised into a resume stack to skip the current cherry-pick and continue.
    """


class SavePatch(Resume):
    """
    Raised into a resume stack to save the current cherry-pick commit as a patch
    file and continue with remaining commits.
    """


class Defer(Resume):
    """
    Raised into a resume stack to put the current conflict down as a commit of
    its own, so the cherry can be applied unchanged.
    """


DEFER_TYPE = "defer"

# marks a deferred commit in `git log`, where the trailer is not visible
DEFER_MARK = "\N{PUSHPIN}"

defer_message = """\
{mark} defer conflict with {title}

{abbrev} conflicts here, so this restores the content it was written
against.  It and everything above now apply with their original deltas.

This commit is the inverse of the change that caused the conflict.  Swap
it up the stack to put it where it belongs.
"""


def undefer_message(message: str) -> str:
    """
    Strip what marks a message as a defer commit's.

    For when two commits are combined into one: the result is an ordinary
    commit, whatever went into it, and should neither be treated as a
    checkpoint nor read like one.
    """
    message = re.sub(rf"^{re.escape(DEFER_MARK)} ", "", message, flags=re.MULTILINE)
    return strip_trailer(message, GITQ_TYPE)


def is_defer(commit: Commit) -> bool:
    "True if this commit pins a tree, rather than carrying a delta."
    return commit.gitq_type == DEFER_TYPE


def restore_tree(ref: Sha, *, git: Git, keep: Sequence[str] = ()) -> None:
    """
    Put the index and working tree back to `ref`'s tree.

    `keep` names paths to hold at their current content instead.  It is for a
    tool's own bookkeeping, which is not part of the history being replayed.
    """
    head = git.sha("HEAD")
    git.checkout_tree(ref)
    for path in keep:
        if git.ref_exists(f"{head}:{path}"):
            git("checkout", head, "--", path)


def defer_conflict(ref: Sha, *, git: Git, keep: Sequence[str] = ()) -> None:
    """
    Commit the tree the conflicting cherry was written against, so that it
    applies with its original delta.

    Everything above replays unchanged too: each cherry then lands on exactly
    its own parent's content.  What the conflict was really about ends up in
    this one commit, which the user can swap up the stack at their leisure.
    """
    cherry = git.commit(ref)
    if not cherry.parents:
        raise UserError(f"Cannot defer: {cherry.summary} is a root commit")
    try:
        parent = git.unique_parent(cherry)
    except MergeFound as e:
        raise UserError(f"Cannot defer: {e}") from e

    restore_tree(parent.sha, git=git, keep=keep)

    if not git.has_staged_changes():
        raise UserError(f"Nothing to defer: {cherry.summary} already applies")

    message = defer_message.format(mark=DEFER_MARK, title=cherry.title, abbrev=cherry.abbrev)
    git.cmd(["git", "commit", "--quiet", "-m", add_trailer(message, GITQ_TYPE, DEFER_TYPE)])
    cherry_pick(cherry, git=git)


def carry_tree_forward(commit: Commit, *, git: Git, keep: Sequence[str] = ()) -> None:
    """
    Re-create `commit` by restoring its tree, rather than replaying its delta.

    A defer commit records the content the commits above it were written
    against, so its delta is only meaningful where it was made.   Replaying it
    as a diff would conflict with whatever changed below; putting its tree back
    exactly is what makes everything above it apply unchanged, which is the
    whole point of having deferred the conflict in the first place.
    """
    with Heading(f"Carrying forward {commit.summary}", quiet=True):
        restore_tree(commit.sha, git=git, keep=keep)
        git.cmd(
            ["git", "commit", "--quiet", "--allow-empty", "--reuse-message", commit.sha],
            comment=commit.title,
        )


def check_unstaged(git: Git) -> None:
    """
    Suspend if the user left changes unstaged.

    Continuing with a dirty working tree either fails outright or quietly
    drops whatever the user forgot to stage, and the error recovery from
    that resets the branch, throwing their work away for good.
    """
    if unstaged := git.unstaged_files():
        raise Suspend(
            status="You have unstaged changes:\n"
            + "".join(f"\t{path}\n" for path in unstaged)
            + "Stage them with `git add`, or discard them."
        )


def head_before_pick(git: Git) -> Sha | None:
    "The commit a cherry-pick is about to land on, if HEAD is born yet."
    return None if git.on_orphan_branch() else git.sha("HEAD")


def finish_conflicted_pick(ref: Sha, head: Sha | None, *, git: Git) -> None:
    """
    Pick up after the user has resolved a conflict.

    If they ran `git cherry-pick --continue` themselves there is nothing left
    to do but check they left the tree clean, and if they didn't, do it here.

    But `git reset` clears the cherry-pick state along with the index, so a
    reset part way through a resolution leaves nothing to say the commit was
    ever being applied.  Carrying on from there would quietly drop it, so
    compare HEAD against where it was when we suspended.
    """
    if git.cherry_pick_in_progress:
        if git.has_unmerged_files():
            Output.print("The index still has unmerged files.")
            raise Suspend(status="Resolve the conflicts.")
        check_unstaged(git)
        finish_cherry_pick(git)
        return
    if head is not None and git.sha("HEAD") == head:
        raise UserError(
            f"Cannot continue: {git.commit(ref).summary}\n"
            "was never applied, and git is no longer cherry-picking it --\n"
            "`git reset` clears that state along with the index.\n"
            "Continuing from here would quietly drop the commit, so abort the\n"
            "operation, or skip this commit, and start over."
        )
    check_unstaged(git)


def finish_cherry_pick(git: Git) -> None:
    """
    Finish a conflicted cherry-pick.

    If the conflict resolution left nothing to commit, `git cherry-pick
    --continue` refuses to proceed, so make the empty commit ourselves.
    Cherries are picked with --allow-empty, so keeping it is consistent,
    and the user can always `--skip` instead.
    """
    if git.has_staged_changes():
        git.cmd(["git", "cherry-pick", "--continue"])
    else:
        Output.print("# the conflict resolution left this commit empty")
        git.cmd(["git", "commit", "--quiet", "--allow-empty", "--no-edit"])


@dataclass
class CherryPickContinue(Continuation):
    """
    When resuming, check if the user ran `git cherry-pick --continue`, and
    do it for them if they have't.
    """

    ref: Sha
    head: Sha | None = field(default=None)

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            with handles(Skip, Defer):
                yield
        except Skip:
            self.git.cherry_pick_abort()
            return
        except Defer:
            self.git.cherry_pick_abort()
            defer_conflict(self.ref, git=self.git)
            return
        except (Exception, Resume):
            self.git.cherry_pick_abort()
            raise
        finish_conflicted_pick(self.ref, self.head, git=self.git)


def cherry_pick(cherry: Commit, *, edit: bool = False, git: Git) -> None:
    "Cherry-pick a single commit.   If it fails, suspend so the user can resolve conflicts."
    with Heading(f"Cherry picking {cherry.summary}", quiet=True):
        try:
            git.cmd(
                ["git", "cherry-pick", "--quiet", "--allow-empty", cherry.sha],
                comment=cherry.title,
            )
        except GitFailed:
            if edit and git.cherry_pick_in_progress:
                with CherryPickContinue(ref=cherry.sha, head=head_before_pick(git)):
                    raise Suspend(status="Resolve the conflicts.")
            else:
                git.cherry_pick_abort()
                raise


class Step(YAMLObject):

    yaml_loader = Loader
    yaml_dumper = Dumper

    @abstractmethod
    def run(self):
        pass


@dataclass
class Then(Continuation):
    "Perform a list of steps in order"

    steps: List[Step]

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        while self.steps:
            self.steps.pop(0).run()


def progn(*steps: Step):
    with Then(steps=list(steps)):
        pass


@dataclass
class Heading(Continuation):

    message: str
    quiet: bool | None = None

    @contextmanager
    def impl(self) -> Iterator:
        if self.quiet:
            yield
        else:
            with Output.heading(self.message):
                yield
