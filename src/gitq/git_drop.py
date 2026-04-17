#!/usr/bin/env python3

import sys
import argparse

from . import continuations
from .continuations import EditBranch, PickCherries, Abort
from .git import UserError
from .git_swap import collect_cherries, CheckoutBaseline


description = """
Delete a commit from history, replaying all commits above it.
"""


class Main(continuations.Main):

    tool = "git-drop"
    continue_command = "git drop --continue"

    def main(self):
        parser = argparse.ArgumentParser(description=description)
        parser.add_argument("commit", nargs="?", metavar="COMMIT")
        parser.add_argument("--continue", "-c", action="store_true", dest="resume")
        parser.add_argument("--edit", "-e", action="store_true")
        parser.add_argument("--abort", action="store_true")
        args = parser.parse_args()

        if args.resume:
            self.resume(None)
            return

        if args.abort:
            self.resume(Abort())
            return

        if not args.commit:
            parser.print_usage()
            sys.exit(1)

        with self.setup():
            commit = self.git.commit(args.commit)
            if commit.is_merge:
                raise UserError("cannot drop a merge commit")
            cherries = collect_cherries(commit, git=self.git)
            parent = self.git.unique_parent_or_root(commit)
            with EditBranch(message="git-drop"):
                with CheckoutBaseline(parent.sha if parent else None):
                    with PickCherries(cherries=cherries, edit=args.edit):
                        pass


main = Main()

if __name__ == "__main__":
    main()
