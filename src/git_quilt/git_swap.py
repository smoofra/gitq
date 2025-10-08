#!/usr/bin/env python3

import os
import sys
from contextlib import contextmanager
from typing import List, Optional, Iterator, Set, TypeVar
import argparse
from textwrap import dedent

from .continuations import Continuation, Suspend, Stop, Squash
from .git import Git, UserError, GitFailed, FNULL

T = TypeVar("T")


class SwapFailed(Exception):
    pass


class MergeFound(Exception):
    pass


# Detach from the current branch, so it can be edited without polluting the
# reflog with a bunch of intermediate steps.   At the end, update the branch and
# check it back out again.
class EditBranch(Continuation[str]):

    def __init__(self, git: Git, *, head: Optional[str] = None) -> None:
        super().__init__(git)
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
        except Exception:
            self.git.force_checkout(self.branch or self.head)
            raise
        else:
            if self.branch:
                self.git.cmd(["git", "update-ref", "-m", "git-swap", self.head, "HEAD"])
                self.git.checkout(self.branch)


# Yield and then pick a list of cherries.
class PickCherries(Continuation):

    def __init__(self, git: Git, *, cherries: List[str]):
        super().__init__(git)
        self.cherries = cherries

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        for cherry in self.cherries:
            cherry_pick(cherry, git=self.git)


# Pick a cherry, resolving conflicts using a reference commit.  When we swap the
# order of two commits, we want the resulting tree to be the same.  This means
# the user should only need to resolve conflicts once, when the now-first commit
# is applied.
class PickCherryWithReference(Continuation):

    def __init__(self, git: Git, *, cherry: str, reference: str):
        super().__init__(git)
        self.cherry = cherry
        self.reference = reference

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        ok = False
        try:
            try:
                self.git.cmd(["git", "cherry-pick", self.cherry], stderr=FNULL)
                ok = True
            except GitFailed:
                if self.git.conflicted:
                    self.git.cmd(["git", "checkout", self.reference, "."])
                    self.git.cmd(["git", "cherry-pick", "--continue"])
                    ok = True
                else:
                    raise
        finally:
            if not ok:
                self.git.cmd(["git", "cherry-pick", "--abort"])
            elif not self.git.cmd_test(["git", "diff", "--quiet", "HEAD", self.reference]):
                # cherry pick succeeded, but resulted in a different tree.  Fix it.
                self.git.cmd(["git", "read-tree", self.reference])
                self.git.cmd(["git", "commit", "--allow-empty", "--amend", "-C", "HEAD"])
                self.git.cmd(["git", "checkout", "-f", "HEAD"])


# When resuming, check if the user ran `git cherry-pick --continue`, and do it for
# them if they have't.
class CherryPickContinue(Continuation):

    def __init__(self, git: Git):
        super().__init__(git)

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self.git.cmd(["git", "cherry-pick", "--abort"])
            raise
        else:
            if self.git.conflicted:
                self.git.cmd(["git", "cherry-pick", "--continue"])


# Handle the case when the user calls `git swap --squash`.
class OrSquash(Continuation):

    def __init__(self, git: Git):
        super().__init__(git)

    @staticmethod
    def edit():
        (_, _, A, B, path) = sys.argv
        with open(path, "w") as f:
            print("pick", A, file=f)
            print("squash", B, file=f)

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
            return
        except Squash:
            pass
        A = self.git.commit("HEAD")
        B = self.git.unique_parent(A)
        C = self.git.unique_parent(B)
        try:
            self.git.cmd(
                [
                    "git",
                    "-c",
                    f"sequence.editor={sys.executable} {__file__} --edit-rebase {B.sha} {A.sha}",
                    "rebase",
                    "-i",
                    C.sha,
                ],
                interactive=True,
            )
        except GitFailed:
            self.git.cmd(["git", "rebase", "--abort"])
            raise
        raise Stop


# restore git state if swap failed
class SwapCheckpoint(Continuation):

    def __init__(self, git: Git, head: str):
        super().__init__(git)
        self.head = head

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self.git.force_checkout(self.head)
            raise


# after ...AB as been swapped to ...BA, keep trying to push B down further
class KeepGoing(Continuation):

    def __init__(self, git: Git, *, edit: bool = False, baselines: Set[str]):
        super().__init__(git)
        self.edit = edit
        self.baselines = baselines

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield  # swap
        except (SwapFailed, Stop):
            return
        A = self.git.commit("HEAD")
        B = self.git.unique_parent(A)
        self.git.checkout(B.sha)
        with PickCherries(self.git, cherries=[A.sha]):
            with KeepGoing(self.git, edit=self.edit, baselines=self.baselines):
                swap_or_squash(edit=self.edit, git=self.git, baselines=self.baselines)


# wrap with KeepGoing if the user specified `--keep-going`
@contextmanager
def maybe_keep_going(
    keep_going: bool, *, edit: bool, git: Git, baselines: Set[str]
) -> Iterator[None]:
    if keep_going:
        with KeepGoing(git, edit=edit, baselines=baselines):
            yield
    else:
        yield


# move HEAD to the specified commit, yield, then cherry-pick everything above it
@contextmanager
def collect_cherries(commit: Optional[str], *, git: Git) -> Iterator[None]:
    if not commit:
        yield
        return
    sha = git.rev_parse(commit)
    cherries: List[str] = list()
    head = git.commit("HEAD")
    while True:
        if head.sha == sha:
            break
        cherries.append(head.sha)
        try:
            head = git.unique_parent(head)
        except MergeFound as e:
            raise UserError(f"Error: {e}") from e
    git.checkout(sha)
    with PickCherries(git, cherries=list(reversed(cherries))):
        yield


# Perform a single cherry pick operation.  This is the only place Suspend can be
# raised.
def cherry_pick(ref: str, *, edit: bool = False, git: Git) -> None:
    try:
        git.cmd(["git", "cherry-pick", ref])
    except GitFailed:
        if edit and git.conflicted:
            with CherryPickContinue(git):
                raise Suspend
        else:
            git.cmd(["git", "cherry-pick", "--abort"])
            raise


# swap HEAD with HEAD^
def swap(*, git: Git, edit: bool = False, baselines: Set[str]) -> None:
    one = git.commit("HEAD")
    try:
        two = git.unique_parent(one)
        three = git.unique_parent(two)
    except MergeFound as e:
        raise SwapFailed(f"Swap failed: {e}") from e
    if two.sha in baselines:
        raise SwapFailed("hit baseline")
    with SwapCheckpoint(git, head=one.sha):
        git.checkout(three.sha)
        with PickCherryWithReference(git, cherry=two.sha, reference=one.sha):
            try:
                cherry_pick(one.sha, edit=edit, git=git)
            except GitFailed as e:
                raise SwapFailed("Swap failed.") from e
            except Suspend as e:
                e.status = dedent(
                    f"""
                Attempting to swap:
                    {one.summary}
                    {two.summary}
                """
                )
                raise


# swap HEAD or HEAD^, or squash them together if the user resumes with `--squash`
def swap_or_squash(*, edit: bool = False, git: Git, baselines: Set[str]) -> None:
    with OrSquash(git):
        swap(edit=edit, git=git, baselines=baselines)


def main() -> None:

    if len(sys.argv) > 1 and sys.argv[1] == "--edit-rebase":
        OrSquash.edit()
        return

    parser = argparse.ArgumentParser(description="swap the order of commits")
    parser.add_argument(
        "--keep-going",
        "-k",
        action="store_true",
        help="push COMMIT as far down the stack as it will go",
    )
    parser.add_argument(
        "--continue",
        action="store_true",
        dest="resume",
        help="resume after conflicts have been resolved",
    )
    parser.add_argument(
        "--abort", action="store_true", help="give up and restore git to original state"
    )
    parser.add_argument(
        "--stop", action="store_true", help="abandon the latest swap operation, and continue"
    )
    parser.add_argument(
        "--squash", action="store_true", help="squash instead of completing this swap"
    )
    parser.add_argument(
        "--edit",
        "-e",
        action="store_true",
        help="if conflicts arise, suspend so the user can resolve them",
    )
    parser.add_argument("--status", action="store_true", help="print status")
    parser.add_argument(
        "commit", nargs="?", metavar="COMMIT", help="swap COMMIT with COMMIT^. defaults to HEAD"
    )
    args = parser.parse_args()

    if sum(bool(x) for x in (args.resume, args.abort, args.stop, args.squash, args.status)) > 1:
        parser.error("use only one of --continue, --abort, --stop, --status, or --squash")

    try:
        git = Git()

        if args.status:
            Continuation.status(git)
            return

        if args.resume or args.abort or args.stop or args.squash:
            Continuation.resume(git, abort=args.abort, stop=args.stop, squash=args.squash)
            return

        if os.path.exists(git.swap_json):
            raise UserError("Error: git swap operation is already in progress")

        if not git.is_clean():
            raise UserError("Error: repo not clean")

        with Continuation.main(git):
            with EditBranch(git) as branch:
                baselines = git.baselines(branch)
                with collect_cherries(args.commit, git=git):
                    with maybe_keep_going(
                        args.keep_going, git=git, edit=args.edit, baselines=baselines
                    ):
                        swap_or_squash(edit=args.edit, git=git, baselines=baselines)

    except (SwapFailed, UserError) as e:
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
