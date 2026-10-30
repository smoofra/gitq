#!/usr/bin/env python3

import sys
import argparse

from . import continuations
from .continuations import EditBranch, PickCherries, Abort, Skip, Defer
from .git import UserError
from .git_swap import collect_cherries, checkout_baseline


description = """
Delete a commit from history, replaying all commits above it.

If replaying a commit conflicts, --skip drops it too, and --defer puts the
conflict down as a commit of its own, so that commit and everything above
it apply unchanged.
"""


class Main(continuations.Main):

    tool = "git-drop"
    continue_command = "git drop --continue"

    def main(self):
        parser = argparse.ArgumentParser(description=description)
        parser.add_argument("commit", nargs="?", metavar="COMMIT")
        parser.add_argument("--continue", "-c", action="store_true", dest="resume")
        parser.add_argument("--edit", "-e", action="store_true", default=True)
        parser.add_argument("--no-edit", action="store_false", dest="edit")
        parser.add_argument("--abort", action="store_true")
        parser.add_argument(
            "--skip",
            action="store_true",
            help="drop the conflicting commit and continue",
        )
        parser.add_argument(
            "--defer",
            action="store_true",
            help="put the conflict down as a commit of its own, and continue",
        )
        parser.add_argument("--status", action="store_true", help="print status")
        args = parser.parse_args()

        if args.resume:
            self.resume(None)
            return

        if args.abort:
            self.resume(Abort())
            return

        if args.skip:
            self.resume(Skip())
            return

        if args.defer:
            self.resume(Defer())
            return

        if args.status:
            self.status()
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
                with checkout_baseline(parent.sha if parent else None, git=self.git):
                    with PickCherries(cherries=cherries, edit=args.edit):
                        pass


main = Main()

if __name__ == "__main__":
    main()
