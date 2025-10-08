import os
import sys
import json
from typing import Optional, List, Dict, TypeVar, ContextManager, Generic, Iterator
from contextlib import contextmanager

from .git import Git, UserError


T = TypeVar("T")


# Suspend execution and save a stack of continuations in .git/swap.json
class Suspend(BaseException):
    continuations: List["Continuation"]
    status: Optional[str]

    def __init__(self) -> None:
        self.continuations = list()
        self.status = None


# Raised into a resume stack by `git swap --abort`.  This will abort the swap
# operation and restore git to its previous state.
class Abort(Exception):
    pass


# Raised into a resume stack by `git swap --stop`.  This will abandon the
# most recent swap operation and push everything back onto the branch.
class Stop(Exception):
    pass


# raised into a resume stack by `git swap --squash`.   This will replace the
# most recent swap operation with a squash, and then push everything back onto
# the branch.
class Squash(Exception):
    pass


# A metaclass for continuation types.  This just collects a dict of them all
# indexed by name.
class ContinuationClass(type):
    types: Dict[str, "ContinuationClass"] = dict()

    def __new__(cls, name, bases, attrs):
        T = type.__new__(cls, name, bases, attrs)
        cls.types[name] = T
        return T


# A continuation is  is a context manager that can be suspended, serialized out
# to json, and then resumed in a subsequent execution of this program.
#
# A continuation class must:
#
#   * have only json-serializable attributes
#
#   * have a 1-1 correspondence between those attributes and `__init__`
#     keywords
#
#   * perform no side effects in `__init__`, EXCEPT as a result of normalizing
#     those attributes.  For example, `EditBranch` takes an optional argument
#     `head`.   If `head is None`, then it does an effectful initialization, and
#     sets `head` to something.   If `head is not None`, then no initialization
#     is performed.
#
#   * implement a context manager overriding .impl()
#
#   * perform no side effects in impl() prior to yield
#
class Continuation(Generic[T], metaclass=ContinuationClass):

    manager: ContextManager[T]
    git: Git

    def __init__(self, git: Git) -> None:
        self.git = git

    def __enter__(self) -> T:
        self.manager = self.impl()
        return self.manager.__enter__()

    def __exit__(self, typ, value, traceback) -> bool | None:
        if typ and issubclass(typ, Suspend):
            if value is None:
                value = typ()
            value.continuations.append(self)
            return None
        else:
            return self.manager.__exit__(typ, value, traceback)

    def impl(self) -> ContextManager[T]:
        # any initialization that occurs before yield should be done in __init__, not here
        raise NotImplementedError

    @classmethod
    def resume(
        cls, git: Git, *, abort: bool = False, stop: bool = False, squash: bool = False
    ) -> None:

        if not os.path.exists(git.swap_json):
            raise UserError("Error: no git swap operation is in progress")

        def r(ks: List[Dict]) -> None:
            if not len(ks):
                if abort:
                    raise Abort
                elif stop:
                    raise Stop
                elif squash:
                    raise Squash
                else:
                    return
            [k, *ks] = ks
            T = ContinuationClass.types[k["kind"]]
            del k["kind"]
            with T(git, **k):
                r(ks)

        with open(git.swap_json, "r") as f:
            j = json.load(f)
        os.unlink(git.swap_json)

        with cls.main(git):
            try:
                r(j["continuations"])
            except (Abort, Stop):
                pass

    @staticmethod
    @contextmanager
    def main(git: Git) -> Iterator[None]:
        if os.path.exists(git.swap_json):
            raise UserError("git-swap operation is already in progress")
        try:
            yield
        except Suspend as e:
            if e.status:
                print(e.status)
            with open(git.swap_json, "w") as f:
                ks = [k.to_json_dict() for k in reversed(e.continuations)]
                j: Dict
                j = {"continuations": ks}
                if e.status:
                    j["status"] = e.status
                json.dump(j, f, indent=True)
                f.write("\n")
            print("Suspended!  Resolve conflicts and run: git swap --continue")
            sys.exit(2)

    @staticmethod
    def status(git: Git) -> None:
        if os.path.exists(git.swap_json):
            with open(git.swap_json, "r") as f:
                print(json.load(f).get("status", "unknown"))
        else:
            print("no swap operation in progress")

    def to_json_dict(self) -> Dict:
        j = self.__dict__
        j["kind"] = self.__class__.__name__
        del j["git"]
        del j["manager"]
        return j
