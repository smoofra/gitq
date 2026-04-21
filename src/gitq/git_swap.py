#!/usr/bin/env python3

import os
import sys
from contextlib import contextmanager
from typing import List, Optional, Iterator, TypeVar, NoReturn
import argparse
from dataclasses import dataclass, field

from .continuations import (
    Abort,
    CheckoutBaseline,
    cherry_pick,
    Continuation,
    EditBranch,
    PickCherries,
    Resume,
    Heading,
)
from . import continuations
from .output import Output
from .git import Git, UserError, GitFailed, MergeFound, split_author, Commit, Sha
from .queue import Queue

description = """Swaps COMMIT with COMMIT^ (i.e. moves COMMIT one step earlier in history),
while holding the final content constant.

This tool re-orders commits like git rebase -i, but is easier to use. In
particular, conflicts must often be resolved twice when using rebase, but
not with git-swap.
"""


T = TypeVar("T")


class SwapFailed(Exception):
    "Swap Failed."


class Stop(Resume):
    """
    Raised into a resume stack by `git swap --stop`.  This will abandon the
    most recent swap operation and push everything back onto the branch.
    """


class Squash(Resume):
    """
    Raised into a resume stack by `git swap --squash`.   This will replace
    the most recent swap operation with a squash, and then push everything
    back onto the branch.
    """


class Fixup(Resume):
    """
    Raised into a resume stack by `git swap --fixup`.   This will replace
    the most recent swap operation with a fixup, and then push everything
    back onto the branch.
    """


@dataclass
class PickCherryWithReference(Continuation):
    """
    Pick a cherry, resolving conflicts using a reference commit.  When we swap the
    order of two commits, we want the resulting tree to be the same.  This means
    the user should only need to resolve conflicts once, when the now-first commit
    is applied.
    """

    cherry: Sha
    reference: Sha

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        self.git.checkout_tree(self.reference)
        self.git("commit", "--allow-empty", "--reuse-message", self.cherry)


@dataclass
class OrSquash(Continuation):
    "Handle the case when the user calls `git swap --squash`, etc.."

    head: Sha
    stop: bool

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except Fixup:
            A = self.git.commit(self.head)
            B = self.git.unique_parent(A)
            C = self.git.unique_parent_or_root(B)
            with CheckoutBaseline(C.sha if C else None):
                self.git.checkout_tree(A.sha)
                self.git.cmd(["git", "commit", "--allow-empty", "--reuse-message", B.sha])
            if self.stop:
                raise Stop
        except Squash:
            A = self.git.commit(self.head)
            B = self.git.unique_parent(A)
            C = self.git.unique_parent_or_root(B)
            with CheckoutBaseline(C.sha if C else None):
                self.git.checkout_tree(A.sha)
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
                cmd = ["git", "commit", "--allow-empty", "--edit", "-F", message]
                self.git.cmd(cmd, env=env, interactive=True)
            if self.stop:
                raise Stop
        except Stop:
            raise  # handled by KeepGoing
        except Resume:
            raise NotImplementedError


@dataclass
class SwapCheckpoint(Continuation):
    "Restore git state if swap failed."

    head: Sha

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield
        except (Exception, Resume):
            Output.print("# reset back to before attempted swap")
            self.git.force_checkout(self.head)
            raise


@dataclass
class KeepGoing(Continuation):
    "After ...AB has been swapped to ...BA, keep trying to push B down further."

    baselines: List[Sha]
    cherries: List[Sha] = field(default_factory=list)
    edit: bool = field(default=False)

    @contextmanager
    def impl(self) -> Iterator[None]:
        try:
            yield  # swap

            while True:
                A = self.git.commit("HEAD")
                B = self.git.unique_parent(A)
                self.cherries = [A.sha] + self.cherries
                self.git.checkout(B.sha)
                swap_or_squash(edit=self.edit, git=self.git, baselines=self.baselines, stop=True)

        except (SwapFailed, MergeFound, Stop):
            for cherry in self.cherries:
                cherry_pick(self.git.commit(cherry))
            return


@dataclass
class KeepGoingUp(Continuation):

    cherries: List[Sha]
    edit: bool = field(default=False)

    @contextmanager
    def impl(self) -> Iterator:
        try:
            yield  # check out base commit
            while self.cherries:
                cherry, *self.cherries = self.cherries
                self.git.cmd(["git", "cherry-pick", "--quiet", "--allow-empty", cherry])
                swap_or_squash(git=self.git, edit=self.edit, baselines=[], stop=True)
        except (Stop, SwapFailed):
            pass
        for cherry in self.cherries:
            self.git.cmd(["git", "cherry-pick", "--quiet", "--allow-empty", cherry])


def collect_cherries(commit: Optional[Commit], *, git: Git) -> List[Sha]:
    if not commit:
        return list()
    cherries: List[Sha] = list()
    head = git.commit("HEAD")
    while True:
        if head.sha == commit.sha:
            return list(reversed(cherries))
        cherries.append(head.sha)
        try:
            head = git.unique_parent(head)
        except MergeFound as e:
            raise UserError(f"Error: {e}") from e


@contextmanager
def edit_commit(commit: Optional[Commit], *, git: Git, edit: bool = False):
    "Move HEAD to the specified commit, yield, then cherry-pick everything above it."
    if not commit:
        yield
        return
    cherries = collect_cherries(commit, git=git)
    git.checkout(commit.sha)
    with PickCherries(cherries=cherries, edit=edit):
        yield


def swap(*, git: Git, edit: bool = False, baselines: List[Sha]) -> None:
    "Swap HEAD with HEAD^."
    one = git.commit("HEAD")
    try:
        two = git.unique_parent(one)
        three = git.unique_parent_or_root(two)
    except MergeFound as e:
        raise SwapFailed(f"Swap failed: {e}") from e
    if two.sha in baselines:
        raise SwapFailed("hit baseline")

    heading = f"Attempting to swap:\n\t{one.summary}\n\t{two.summary}"
    with Heading(heading, quiet=True):
        with SwapCheckpoint(head=one.sha):
            with CheckoutBaseline(three.sha if three else None):
                with PickCherryWithReference(cherry=two.sha, reference=one.sha):
                    try:
                        cherry_pick(one, edit=edit)
                    except GitFailed as e:
                        raise SwapFailed(f"Swap failed: {e}") from e


def swap_or_squash(*, edit: bool = False, git: Git, baselines: List[Sha], stop: bool) -> None:
    "Swap HEAD or HEAD^, or squash them together if the user resumes with `--squash`."
    head = git.commit("HEAD")
    with OrSquash(head=head.sha, stop=stop):
        swap(edit=edit, git=git, baselines=baselines)


class Main(continuations.Main):

    tool = "git-swap"
    continue_command = "git swap --continue"

    def __call__(self) -> NoReturn:
        try:
            super().__call__()
            sys.exit(1)
        except SwapFailed as e:
            Output.print(e)
            sys.exit(1)

    def main(self) -> None:

        parser = argparse.ArgumentParser(
            description=description, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser.add_argument(
            "--keep-going",
            "-k",
            action="store_true",
            help="push COMMIT as far down (or up) the stack as it will go",
        )
        parser.add_argument(
            "--continue",
            "-c",
            action="store_true",
            dest="resume",
            help="resume after conflicts have been resolved",
        )
        parser.add_argument(
            "--up", action="store_true", help="swap the given commit with the one above it"
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
            "--force",
            action="store_true",
            help="allow swap past the upstream, ie HEAD@{u}",
        )
        parser.add_argument(
            "--edit",
            "-e",
            action="store_true",
            help="if conflicts arise, suspend so the user can resolve them",
        )
        parser.add_argument("--status", action="store_true", help="print status")
        parser.add_argument(
            "commit",
            nargs="?",
            metavar="COMMIT",
            help="swap COMMIT with COMMIT^. defaults to HEAD",
        )
        args = parser.parse_args()

        mode_args = (args.resume, args.abort, args.stop, args.squash, args.fixup, args.status)
        if sum(bool(x) for x in mode_args) > 1:
            parser.error("use only one of --continue, --abort, --stop, --status, or --squash")

        if args.status:
            self.status()
            return

        if args.resume or args.abort or args.stop or args.squash or args.fixup:
            resume: BaseException | None = None
            if args.abort:
                resume = Abort()
            elif args.stop:
                resume = Stop()
            elif args.squash:
                resume = Squash()
            elif args.fixup:
                resume = Fixup()
            self.resume(resume)
            return

        with self.setup():
            upstream = self.git.upstream("HEAD")
            with EditBranch(message="git-swap"):
                if args.up:
                    self.swap_up(args)
                else:
                    if Queue.is_queue(self.git):
                        baselines = list(Queue(self.git).baselines_for_swap())
                    elif upstream and not args.force:
                        baselines = [upstream]
                    else:
                        baselines = []
                    self.swap_down(args, baselines)

    def swap_down(self, args, baselines: List[Sha]) -> None:
        commit = self.git.commit(args.commit) if args.commit else None
        with edit_commit(commit, git=self.git):
            if args.keep_going:
                with KeepGoing(edit=args.edit, baselines=baselines):
                    swap_or_squash(edit=args.edit, git=self.git, baselines=baselines, stop=True)
            else:
                swap_or_squash(edit=args.edit, git=self.git, baselines=baselines, stop=False)

    def swap_up(self, args) -> None:
        if not args.commit:
            raise UserError("specify a commit")
        commit = self.git.commit(args.commit)
        cherries = collect_cherries(commit, git=self.git)
        if not cherries:
            raise UserError("commit is already at HEAD")
        if args.keep_going:
            with KeepGoingUp(edit=args.edit, cherries=cherries):
                self.git.checkout(commit.sha)
        else:
            with edit_commit(self.git.commit(cherries[0]), git=self.git):
                swap_or_squash(edit=args.edit, git=self.git, baselines=[], stop=False)


main = Main()

if __name__ == "__main__":
    main()
