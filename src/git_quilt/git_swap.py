#!/usr/bin/env python3

import os
import sys
from contextlib import contextmanager
from typing import List, Optional, Iterator, TypeVar
import argparse
from textwrap import dedent

from .continuations import Continuation, Suspend, Stop, Squash, Fixup, CheckoutBaseline, Resume
from .git import Git, UserError, GitFailed, MergeFound, split_author

T = TypeVar("T")


class SwapFailed(Exception):
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
        except (Exception, Resume):
            print("# git-swap has failed.  resetting to original HEAD")
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
        self.git.cmd(["git", "read-tree", self.reference])
        self.git.cmd(["git", "commit", "--allow-empty", "--reuse-message", self.cherry])
        self.git.cmd(["git", "reset", "--hard", "HEAD"])


# When resuming, check if the user ran `git cherry-pick --continue`, and do it for
# them if they have't.
class CherryPickContinue(Continuation):

    def __init__(self, git: Git):
        super().__init__(git)

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except (Exception, Resume):
            if self.git.cherry_pick_in_progress:
                if self.git.on_orphan_branch():
                    self.git.log_cmd(["rm", self.git.gitdir / "CHERRY_PICK_HEAD"])
                    (self.git.gitdir / "CHERRY_PICK_HEAD").unlink()
                    self.git.delete_index_and_files()
                else:
                    self.git.cmd(["git", "cherry-pick", "--abort"])

            raise
        else:
            if self.git.cherry_pick_in_progress:
                self.git.cmd(["git", "cherry-pick", "--continue"])


# Handle the case when the user calls `git swap --squash`, etc..
class OrSquash(Continuation):

    def __init__(self, git: Git, head: str):
        self.head = head
        super().__init__(git)

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except Fixup:
            A = self.git.commit(self.head)
            B = self.git.unique_parent(A)
            C = self.git.unique_parent_or_root(B)
            with CheckoutBaseline(self.git, C.sha if C else None):
                self.git.cmd(["git", "read-tree", A.sha])
                self.git.cmd(["git", "commit", "--allow-empty", "--reuse-message", B.sha])
                self.git.cmd(["git", "reset", "--hard", "HEAD"])
            raise Stop
        except Squash:
            A = self.git.commit(self.head)
            B = self.git.unique_parent(A)
            C = self.git.unique_parent_or_root(B)
            with CheckoutBaseline(self.git, C.sha if C else None):
                self.git.cmd(["git", "read-tree", A.sha])
                author = split_author(B.author)
                env = dict(os.environ)
                env.update(
                    {
                        "GIT_AUTHOR_NAME": author.name,
                        "GIT_AUTHOR_EMAIL": author.email,
                        "GIT_AUTHOR_DATE": author.date,
                    }
                )
                message = self.git.gitdir / "COMMIT_EDITMSG"
                with open(message, "w") as f:
                    f.write(B.message)
                    f.write("\n\n")
                    f.write(A.message)
                self.git.cmd(["git", "commit", "--allow-empty", "--edit", "-F", message], env=env)
                self.git.cmd(["git", "reset", "--hard", "HEAD"])
            raise Stop
        except Stop:
            raise  # handled by KeepGoing
        except Resume:
            raise NotImplementedError


# restore git state if swap failed
class SwapCheckpoint(Continuation):

    def __init__(self, git: Git, head: str):
        super().__init__(git)
        self.head = head

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except (Exception, Resume):
            print("# reset back to before attempted swap")
            self.git.force_checkout(self.head)
            raise


# after ...AB as been swapped to ...BA, keep trying to push B down further
class KeepGoing(Continuation):

    def __init__(self, git: Git, *, edit: bool = False, baselines: List[str]):
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
        try:
            B = self.git.unique_parent(A)
        except MergeFound:
            return
        self.git.checkout(B.sha)
        with PickCherries(self.git, cherries=[A.sha]):
            with KeepGoing(self.git, edit=self.edit, baselines=self.baselines):
                swap_or_squash(edit=self.edit, git=self.git, baselines=self.baselines)


class SingleSwap(Continuation):
    "Absorb Stop exceptions which might be raised by swap"

    @contextmanager
    def impl(self):
        try:
            yield
        except Stop:
            pass


# wrap with KeepGoing if the user specified `--keep-going`
@contextmanager
def maybe_keep_going(
    keep_going: bool, *, edit: bool, git: Git, baselines: List[str]
) -> Iterator[None]:
    if keep_going:
        with KeepGoing(git, edit=edit, baselines=baselines):
            yield
    else:
        with SingleSwap(git):
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
        git.cmd(["git", "cherry-pick", "--allow-empty", ref])
    except GitFailed:
        if edit and git.cherry_pick_in_progress:
            with CherryPickContinue(git):
                raise Suspend
        else:
            git.cmd(["git", "cherry-pick", "--abort"])
            raise


# swap HEAD with HEAD^
def swap(*, git: Git, edit: bool = False, baselines: List[str]) -> None:
    one = git.commit("HEAD")
    try:
        two = git.unique_parent(one)
        three = git.unique_parent_or_root(two)
    except MergeFound as e:
        raise SwapFailed(f"Swap failed: {e}") from e
    if two.sha in baselines:
        raise SwapFailed("hit baseline")
    with SwapCheckpoint(git, head=one.sha):
        with CheckoutBaseline(git, three.sha if three else None):
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
def swap_or_squash(*, edit: bool = False, git: Git, baselines: List[str]) -> None:
    head = git.commit("HEAD")
    with OrSquash(git, head=head.sha):
        swap(edit=edit, git=git, baselines=baselines)


def main() -> None:

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
        "--fixup", action="store_true", help="fixup instead of completing this swap"
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

    modeargs = (args.resume, args.abort, args.stop, args.squash, args.fixup, args.status)
    if sum(bool(x) for x in modeargs) > 1:
        parser.error("use only one of --continue, --abort, --stop, --status, or --squash")

    try:
        git = Git()

        if args.status:
            Continuation.status(git)
            return

        if args.resume or args.abort or args.stop or args.squash or args.fixup:
            Continuation.resume(
                git, abort=args.abort, stop=args.stop, squash=args.squash, fixup=args.fixup
            )
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
