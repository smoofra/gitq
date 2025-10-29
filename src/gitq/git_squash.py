#!/usr/bin/env python3


import argparse

from . import continuations
from .continuations import EditBranch
from .git_swap import edit_commit, OrSquash, Squash, Fixup, CatchStop


class Main(continuations.Main):

    tool = "git-squash"

    def main(self):
        parser = argparse.ArgumentParser(description="squash a commit into its parent")
        parser.add_argument("commit")
        parser.add_argument("--fixup", "-f", action="store_true")
        args = parser.parse_args()

        with self.setup():
            commit = self.git.commit(args.commit)
            with EditBranch(self.git, message="git-squash"):
                with edit_commit(commit, git=self.git):
                    with CatchStop(self.git):
                        with OrSquash(self.git, head=commit.sha):
                            if args.fixup:
                                raise Fixup
                            else:
                                raise Squash


main = Main()

if __name__ == "__main__":
    main()
