#!/usr/bin/env python3

import sys
import argparse
from contextlib import contextmanager
from typing import Iterator
from dataclasses import dataclass, field

from . import continuations
from .continuations import Continuation, EditBranch, Suspend, Abort
from .git_swap import edit_commit, PickCherryWithReference

description = """
Splits a single commit into two or more commits. The user can add a
additional commits, while holding the final content constant.

Checks out COMMIT with its changes staged,  via `git reset --soft HEAD^`,
suspending so the user can make one or more new commits. After the user
continues, COMMIT will be restored with its original content, and git-split
will suspend again so the user can amend the commit message with
`git commit --amend`.

After continuing again, it replays the remaining commits from above COMMIT
on top.
"""


@dataclass
class SuspendForAmend(Continuation):
    """Suspend after the inner block completes, so the user can run
    `git commit --amend` before continuing."""

    target: str
    done: bool = field(default=False)

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        if not self.done:
            self.done = True
            raise Suspend(
                status=f"Commit {self.target} was split.\n"
                + "Edit the message with `git commit --amend`"
            )


class Main(continuations.Main):

    tool = "git-split"
    continue_command = "git split --continue"

    def main(self):
        parser = argparse.ArgumentParser(
            description=description, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser.add_argument("commit", nargs="?", metavar="COMMIT")
        parser.add_argument(
            "--continue",
            "-c",
            action="store_true",
            dest="resume",
        )
        parser.add_argument(
            "--abort",
            action="store_true",
        )
        parser.add_argument("--status", action="store_true", help="print status")
        args = parser.parse_args()

        if args.resume:
            self.resume(None)
            return

        if args.abort:
            self.resume(Abort())

        if args.status:
            self.status()
            return

        if not args.commit:
            parser.print_usage()
            sys.exit(1)

        with self.setup():
            commit = self.git.commit(args.commit)
            status = (
                f"Splitting {commit.summary}.\n"
                + "Changes from the commit are now staged.\n"
                + "Make one or more commits here."
            )

            with EditBranch(message="git-split"):
                with edit_commit(commit, git=self.git, edit=True):
                    with SuspendForAmend(commit.sha):
                        with PickCherryWithReference(cherry=commit.sha, reference=commit.sha):
                            self.git("reset", "--soft", "HEAD^")
                            raise Suspend(status=status)


main = Main()

if __name__ == "__main__":
    main()
