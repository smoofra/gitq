#!/usr/bin/env python3

import sys
import argparse

from . import continuations
from .continuations import EditBranch, Suspend, Abort
from .git_swap import edit_commit


description = """
Detaches HEAD at COMMIT, suspending so the user can amend it. When done,
resume with `git edit --continue` to replay all commits that were above
COMMIT back on top.
"""


class Main(continuations.Main):

    tool = "git-edit"
    suspend_message = "Suspended! edit HEAD, then resume with `git edit --continue`"

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
                    raise Suspend


main = Main()

if __name__ == "__main__":
    main()
