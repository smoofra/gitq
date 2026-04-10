#!/usr/bin/env python3


import argparse

from . import continuations
from .continuations import EditBranch
from .git_swap import edit_commit, OrSquash, Squash, Fixup

description = """
Combines COMMIT with COMMIT^. Opens an editor to compose the combined
commit message (like git commit --squash).

With --fixup/-f, discards COMMIT's message and keeps only the parent's
(like git commit --fixup).
"""


class Main(continuations.Main):

    tool = "git-squash"

    def main(self):
        parser = argparse.ArgumentParser(
            description=description, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser.add_argument("commit", metavar="COMMIT")
        parser.add_argument("--fixup", "-f", action="store_true")
        args = parser.parse_args()

        with self.setup():
            commit = self.git.commit(args.commit)
            with EditBranch(message="git-squash"):
                with edit_commit(commit, git=self.git):
                    with OrSquash(head=commit.sha, stop=False):
                        if args.fixup:
                            raise Fixup
                        else:
                            raise Squash


main = Main()

if __name__ == "__main__":
    main()
