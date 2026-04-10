#!/usr/bin/env python3

import sys
import argparse
from contextlib import contextmanager
from typing import Iterator
from dataclasses import dataclass, field

from . import continuations
from .continuations import Continuation, EditBranch, Suspend, Abort
from .git_swap import edit_commit, PickCherryWithReference


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
                + "Edit the message with: git commit --amend\n"
                + "Then resume with: git split --continue."
            )


class Main(continuations.Main):

    tool = "git-split"
    suspend_message = "Resume with `git split --continue` when done."

    def main(self):
        parser = argparse.ArgumentParser(description="split a commit")
        parser.add_argument("commit", nargs="?")
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
            sha = commit.sha
            target = self.git.abbrev(sha)
            status = (
                f"Splitting {target}.\n"
                + "Changes from the commit are now staged.\n"
                + "Make one or more commits here, then resume with: git split --continue."
            )

            with EditBranch(message="git-split"):
                with edit_commit(commit, git=self.git, edit=True):
                    with SuspendForAmend(target):
                        with PickCherryWithReference(cherry=sha, reference=sha):
                            self.git("reset", "--soft", "HEAD^")
                            raise Suspend(status=status)


main = Main()

if __name__ == "__main__":
    main()
