#!/usr/bin/env python3

import sys
import argparse

from . import continuations
from .continuations import EditBranch, Suspend
from .git_swap import edit_commit


class Main(continuations.Main):

    tool = "git-edit"
    suspend_message = "Suspended! edit HEAD, then resume with `git edit --continue`"

    def main(self):
        parser = argparse.ArgumentParser(description="edit a commit")
        parser.add_argument("commit", nargs="?")
        parser.add_argument(
            "--continue",
            "-c",
            action="store_true",
            dest="resume",
            help="resume edits have been made",
        )
        parser.add_argument("--status", action="store_true", help="print status")
        args = parser.parse_args()

        if args.resume:
            self.resume(None)
            return

        if args.status:
            self.status()
            return

        if not args.commit:
            parser.print_usage()
            sys.exit(1)

        with self.setup():
            commit = self.git.commit(args.commit)
            with EditBranch(self.git, message="git-edit"):
                with edit_commit(commit, git=self.git, edit=True):
                    raise Suspend


main = Main()

if __name__ == "__main__":
    main()
