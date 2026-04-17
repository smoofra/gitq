import sys
import yaml
from typing import Optional, List, TypeVar, ContextManager, Generic, Iterator, NoReturn, Type
from contextlib import contextmanager
from itertools import count
from abc import abstractmethod
from dataclasses import dataclass, field

from .output import Output
from .git import Git, UserError, GitFailed, contextGit, Commit, Sha
from .yaml import YAMLObject, BaseLoader


class Loader(BaseLoader):
    "YAML loader for .git/continuation.yaml"

    pass


class Dumper(yaml.Dumper):
    "YAML dumper for .git/continuation.yaml"

    pass


@dataclass
class Continuations(YAMLObject):
    "Root object for .git/continuation.yaml"

    yaml_loader = Loader
    yaml_dumper = Dumper

    continuations: List["Continuation"]
    tool: str
    continue_command: str | None = None
    status: str | None = field(default=None)


yaml.add_path_resolver("!Continuations", [], Loader=Loader, Dumper=Dumper)

T = TypeVar("T")


class Suspend(BaseException):
    "Suspend execution and save a stack of continuations in .git/continuation.yaml"

    continuations: List["Continuation"]
    status: Optional[str]

    def __init__(self, *, status: str | None = None) -> None:
        super().__init__()
        self.status = status
        self.continuations = list()


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

    @property
    def git(sef) -> Git:
        return contextGit.get()

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

    @contextmanager
    def setup(self) -> Iterator:
        if not self.git.is_clean():
            raise UserError("Error: repo not clean")
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
            j = Continuations(continuations, self.tool, self.continue_command, e.status)
            yaml.dump(j, f, Dumper=Dumper)
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

        # All the commands support continue and abort, so its fine if the
        # user calls them from the wrong tool.
        if j.tool != self.tool and throw is not None and not isinstance(throw, Abort):
            raise UserError(f"A {j.tool} operation is currently in progress")

        self.git.continuation.unlink()

        try:
            self.reanimate(j.continuations, throw=throw)
        except Suspend as e:
            self.suspend(e)
        except Resume as e:
            raise Exception("Internal error.  Uncaught Resume") from e

        sys.exit(0)

    def status(self) -> None:
        if not self.git.continuation.exists():
            Output.print("no operation in progress")
            return
        with open(self.git.continuation, "r") as f:
            j: Continuations = yaml.load(f, Loader)

        Output.print(f"A {j.tool} operation is in progress.")
        Output.print()
        for c in j.continuations:
            if isinstance(c, Heading):
                Output.print(f" * {c.message}")
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
    def impl(self) -> Iterator[None]:
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
def TempBranch() -> Iterator[str]:
    """
    Create a temporary branch with no content and no parents.
    """
    git = contextGit.get()
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
def CheckoutBaseline(sha: Sha | None):
    """
    Checkout a baseline commit, or if argument is None, create a temporary
    branch with no history and check that out.
    """
    git = contextGit.get()
    if sha is None:
        with TempBranch():
            yield
    else:
        git.checkout(sha)
        yield


@dataclass
class EditBranch(Continuation[str]):
    """
    Detach from the current branch, so it can be edited without polluting
    the reflog with a bunch of intermediate steps.   At the end, update the
    branch using message, and check it back out again.
    """

    message: str
    head: str | None = field(default=None)

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
class CheckoutBranch(Finally):
    "Temporarily checkout ref, then restore to previous HEAD"

    branch: str
    old_branch: str | None = field(default=None)

    def cleanup(self):
        if self.old_branch is not None:
            self.git.force_checkout(self.old_branch, comment="restore previous HEAD")

    @contextmanager
    def impl(self) -> Iterator:
        assert self.branch.startswith("refs/heads/")
        if self.old_branch is None:
            self.old_branch = self.git.head()
            if self.old_branch.startswith("refs/heads/"):
                self.old_branch = self.old_branch.removeprefix("refs/heads/")
            self.git.checkout(self.branch.removeprefix("refs/heads/"), comment="checkout branch")
        with super().impl():
            yield


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
            cherry_pick(self.git.commit(cherry), edit=self.edit)


@dataclass
class CherryPickContinue(Continuation):
    """
    When resuming, check if the user ran `git cherry-pick --continue`, and
    do it for them if they have't.
    """

    ref: Sha

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except (Exception, Resume):
            self.git.cherry_pick_abort()
            raise
        if self.git.cherry_pick_in_progress:
            if self.git.has_unmerged_files():
                Output.print("The index still has unmerged files.")
                raise Suspend(status="Resolve the conflicts.")
            self.git.cmd(["git", "cherry-pick", "--continue"])


def cherry_pick(cherry: Commit, *, edit: bool = False) -> None:
    "Cherry-pick a single commit.   If it fails, suspend so the user can resolve conflicts."
    git = contextGit.get()
    with Heading(f"Cherry picking {cherry.summary}", quiet=True):
        try:
            git.cmd(
                ["git", "cherry-pick", "--quiet", "--allow-empty", cherry.sha],
                comment=cherry.title,
            )
        except GitFailed:
            if edit and git.cherry_pick_in_progress:
                with CherryPickContinue(ref=cherry.sha):
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

    @property
    def git(self) -> Git:
        return contextGit.get()


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
