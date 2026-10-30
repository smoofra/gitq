#!/usr/bin/env python3


import argparse

from . import continuations
from .continuations import EditBranch
from .git import UserError
from .git_swap import (
    collect_cherries,
    edit_commit,
    OrSquash,
    Fixup,
    add_message_args,
    given_message,
    squash_from_args,
)

description = """
Combines COMMIT with COMMIT^. Opens an editor to compose the combined
commit message (like git commit --squash), unless --message, --file, or
--no-edit says otherwise.

With --up, combines COMMIT with the commit after it instead.  The commit
being squashed into is the one that survives, so --up keeps the author of
the later of the two commits, where a plain squash keeps the earlier one's.
The earlier message still comes first either way.

With --fixup/-f, the message of the commit being squashed is discarded and
only the message of the one it is squashed into is kept (like git commit
--fixup).
"""


class Main(continuations.Main):

    tool = "git-squash"

    def main(self):
        parser = argparse.ArgumentParser(
            description=description, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser.add_argument("commit", metavar="COMMIT")
        parser.add_argument("--fixup", "-f", action="store_true")
        parser.add_argument(
            "--up",
            action="store_true",
            help="squash COMMIT into the commit after it, rather than the one before",
        )
        add_message_args(parser, "--no-edit")
        args = parser.parse_args()

        if given_message(args) and args.fixup:
            parser.error("--message, --file and --no-edit are not used with --fixup")

        with self.setup():
            commit = self.git.commit(args.commit)
            if args.up:
                # the same pair of commits as squashing the child down, but
                # the child is the destination, so its identity survives
                above = collect_cherries(commit, git=self.git)
                if not above:
                    raise UserError("commit is already at HEAD")
                commit = self.git.commit(above[0])
            with EditBranch(message="git-squash"):
                with edit_commit(commit, git=self.git):
                    with OrSquash(head=commit.sha, stop=False):
                        if args.fixup:
                            raise Fixup(up=args.up)
                        else:
                            raise squash_from_args(args, up=args.up)


main = Main()

if __name__ == "__main__":
    main()
