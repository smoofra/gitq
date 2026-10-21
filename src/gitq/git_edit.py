#!/usr/bin/env python3

import sys
import argparse
from contextlib import contextmanager
from typing import Iterator
from dataclasses import dataclass

from . import continuations
from .continuations import Continuation, EditBranch, Suspend, Abort, check_unstaged
from .git_swap import edit_commit
from .output import Output


description = """
Detaches HEAD at COMMIT, suspending so the user can amend it. When done,
resume with `git edit --continue` to replay all commits that were above
COMMIT back on top.

Any changes still staged when you continue are amended into COMMIT.  If the
working tree is dirty, git-edit suspends again so you can stage or discard
those changes first.
"""


@dataclass
class AmendStagedChanges(Continuation):
    """
    On resume, fold the user's staged changes into HEAD with `git commit
    --amend`.

    If the working tree is dirty, suspend again instead.  Replaying the
    commits above HEAD on top of a dirty working tree fails, and the
    recovery from that failure throws away whatever the user was working on.
    """

    @contextmanager
    def impl(self) -> Iterator[None]:
        yield
        check_unstaged(self.git)
        if self.git.has_staged_changes():
            Output.print("# folding staged changes into HEAD")
            self.git("commit", "--amend", "--no-edit")


class Main(continuations.Main):

    tool = "git-edit"
    continue_command = "git edit --continue"

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
            help="resume edits have been made",
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
            with EditBranch(message="git-edit"):
                with edit_commit(commit, git=self.git, edit=True):
                    with AmendStagedChanges():
                        raise Suspend(status="Edit HEAD")


main = Main()

if __name__ == "__main__":
    main()
