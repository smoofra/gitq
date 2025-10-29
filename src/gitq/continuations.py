import sys
import json
from typing import Optional, List, Dict, TypeVar, ContextManager, Generic, Iterator, NoReturn
from contextlib import contextmanager
from itertools import count
from abc import abstractmethod

from .git import Git, UserError, GitFailed


T = TypeVar("T")


class Suspend(BaseException):
    "Suspend execution and save a stack of continuations in .git/continuation.json"

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


# A metaclass for continuation types.  This just collects a dict of them all
# indexed by name.
class ContinuationClass(type):
    types: Dict[str, "ContinuationClass"] = dict()

    def __new__(cls, name, bases, attrs):
        T = type.__new__(cls, name, bases, attrs)
        cls.types[name] = T
        return T


# A continuation is  is a context manager that can be suspended, serialized
# out to json, and then resumed in a subsequent execution of this program.
#
# This is a very low-tech approach to serializeable continuations, and it
# relies on suspendable code being written in a strange idiom to work.
#
# Anything that needs to happen after a resume needs to be expressed as a
# stack of `Continuation` instances, rather than ordinary function calls.
#
# A continuation class must:
#
#   * have only json-serializable attributes
#
#   * have a 1-1 correspondence between those attributes and `__init__`
#     keywords
#
#   * perform no side effects in `__init__`, EXCEPT as a result of
#     normalizing those attributes.  For example, `EditBranch` takes an
#     optional argument `head`.   If `head is None`, then it does an
#     effectful initialization, and sets `head` to something.   If `head is
#     not None`, then no initialization is performed.
#
#   * implement a context manager overriding `.impl()`
#
#   * perform no side effects in `.impl()` prior to yield
#
#   * be prepared to reconstruct the execution state of `.impl()`, if it
#     calls anything that might raise Suspend.   In other words, there is
#     no magic here that somehow serializes the python execution state.
#     Each `Continuation` instance is just going to be reanimated based on
#     its json-serializeable attributes, and resume again from the yield.
#
class Continuation(Generic[T], metaclass=ContinuationClass):

    manager: ContextManager[T]
    git: Git

    def __init__(self, git: Git) -> None:
        self.git = git

    def __enter__(self) -> T:
        self.manager = self.impl()
        return self.manager.__enter__()

    def __exit__(self, exception_type, exception, traceback) -> bool | None:
        if exception is None and exception_type is not None:
            exception = exception_type()

        if isinstance(exception, Suspend):
            exception.continuations.append(self)
            return None

        try:
            return self.manager.__exit__(exception_type, exception, traceback)
        except Suspend as exception:
            exception.continuations.append(self)
            raise

    @abstractmethod
    def impl(self) -> ContextManager[T]:
        pass

    def to_json_dict(self) -> Dict:
        j = self.__dict__
        j["kind"] = self.__class__.__name__
        del j["git"]
        del j["manager"]
        return j


class Main:

    tool: str
    suspend_message = "Suspended!"

    @abstractmethod
    def main(self) -> None:
        pass

    def __call__(self) -> NoReturn:
        self.git = Git()
        try:
            self.main()
        except UserError as e:
            print(e)
            sys.exit(1)
        except Abort:
            print("Cancelled.  Previous state restored.")
        sys.exit(0)

    @contextmanager
    def setup(self) -> Iterator:
        if not self.git.is_clean():
            raise UserError("Error: repo not clean")
        if self.git.continuation.exists():
            with open(self.git.continuation, "r") as f:
                j = json.load(f)
            raise UserError(f"{j["tool"]} operation is already in progress.")
        try:
            yield
        except Suspend as e:
            self.suspend(e)
        except Resume as e:
            raise Exception("Internal error.  Uncaught Resume") from e

    def suspend(self, e: Suspend) -> NoReturn:
        if e.status:
            print(e.status)
        with open(self.git.continuation, "w") as f:
            ks = [k.to_json_dict() for k in reversed(e.continuations)]
            j: Dict
            j = {"continuations": ks}
            j["tool"] = self.tool
            if e.status:
                j["status"] = e.status
            json.dump(j, f, indent=True)
            f.write("\n")
        print(self.suspend_message)
        sys.exit(2)

    def reanimate(self, ks: List[Dict], *, throw: BaseException | None) -> None:
        if not len(ks):
            if throw is not None:
                raise throw
            else:
                return
        k, *ks = ks
        T = ContinuationClass.types[k["kind"]]
        del k["kind"]
        with T(self.git, **k):
            self.reanimate(ks, throw=throw)

    def resume(self, throw: BaseException | None = None) -> NoReturn:

        if not self.git.continuation.exists():
            raise UserError(f"Error: no {self.tool} operation is in progress")

        with open(self.git.continuation, "r") as f:
            j = json.load(f)

        if j["tool"] != self.tool:
            raise UserError(f"A {j["tool"]} operation is currently in progress")

        self.git.continuation.unlink()

        try:
            self.reanimate(j["continuations"], throw=throw)
        except Suspend as e:
            self.suspend(e)
        except Resume as e:
            raise Exception("Internal error.  Uncaught Resume") from e

        sys.exit(0)

    def status(self) -> None:
        if not self.git.continuation.exists():
            print("no operation in progress")
            return
        with open(self.git.continuation, "r") as f:
            j = json.load(f)
        if j["tool"] != self.tool:
            raise UserError(f"{j["tool"]} operation is in progress, not {self.tool}")
        print(j.get("status", "unknown"))


class DeleteTempBranch(Continuation):

    def __init__(self, git: Git, branch: str, previous_head: str):
        super().__init__(git)
        self.branch = branch
        self.previous_head = previous_head

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        finally:
            if self.git.on_orphan_branch():
                print(f"# reset back to before creating {self.branch} branch")
                self.git.force_checkout(self.previous_head)
            else:
                self.git.detach()
            if self.git.branch_exists(self.branch):
                self.git.cmd(["git", "branch", "-qD", self.branch])


@contextmanager
def TempBranch(git: Git) -> Iterator[str]:
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

    with DeleteTempBranch(git=git, branch=branch, previous_head=git.head()):
        git.cmd(["git", "checkout", "-q", "--orphan", branch])
        git.delete_index_and_files()
        yield branch


@contextmanager
def CheckoutBaseline(git: Git, sha: str | None):
    """
    Checkout a baseline commit, or if argument is None, create a temporary
    branch with no history and check that out.
    """
    if sha is None:
        with TempBranch(git):
            yield
    else:
        git.checkout(sha)
        yield


class EditBranch(Continuation[str]):
    """
    Detach from the current branch, so it can be edited without polluting
    the reflog with a bunch of intermediate steps.   At the end, update the
    branch using message, and check it back out again.
    """

    def __init__(self, git: Git, *, message: str, head: Optional[str] = None) -> None:
        super().__init__(git)
        self.message = message
        if head:
            self.head = head
        else:
            self.head = git.head()
            git.detach()

    @property
    def branch(self) -> Optional[str]:
        if self.head.startswith("refs/heads/"):
            return self.head.removeprefix("refs/heads/") or None
        return None

    @contextmanager
    def impl(self) -> Iterator[str]:
        try:
            yield self.head
        except (Exception, Resume):
            print("# Failed.  Resetting to original HEAD")
            self.git.force_checkout(self.branch or self.head)
            raise
        else:
            if self.branch:
                self.git.cmd(["git", "update-ref", "-m", self.message, self.head, "HEAD"])
                self.git.checkout(self.branch)


class PickCherries(Continuation):
    "Yield, then cherry-pick specified commits."

    def __init__(self, git: Git, *, cherries: List[str], edit: bool = False):
        super().__init__(git)
        self.cherries = cherries
        self.edit = edit

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        while self.cherries:
            cherry, *self.cherries = self.cherries
            cherry_pick(cherry, git=self.git, edit=self.edit)


class CherryPickContinue(Continuation):
    """
    When resuming, check if the user ran `git cherry-pick --continue`, and
    do it for them if they have't.
    """

    def __init__(self, git: Git, *, ref: str):
        super().__init__(git)
        self.ref = ref

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except (Exception, Resume):
            self.git.cherry_pick_abort()
            raise
        if self.git.cherry_pick_in_progress:
            if self.git.has_unmerged_files():
                print("The index still has unmerged files.")
                raise Suspend(status=f"cherry-picking {self.ref}")
            self.git.cmd(["git", "cherry-pick", "--continue"])


def cherry_pick(ref: str, *, edit: bool = False, git: Git) -> None:
    "Cherry-pick a single commit.   If it fails, suspend so the user can resolve conflicts."
    try:
        git.cmd(["git", "cherry-pick", "--allow-empty", ref])
    except GitFailed:
        if edit and git.cherry_pick_in_progress:
            with CherryPickContinue(git, ref=ref):
                raise Suspend(status=f"cherry-picking {ref}")
        else:
            git.cherry_pick_abort()
            raise
