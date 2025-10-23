#!/usr/bin/env python3


import sys
import argparse

from .continuations import Continuation, EditBranch
from .git import Git, UserError
from .git_swap import edit_commit, OrSquash, Squash, Fixup, CatchStop


def main():
    parser = argparse.ArgumentParser(description="squash a commit into its parent")
    parser.add_argument("commit")
    parser.add_argument("--fixup", "-f", action="store_true")
    args = parser.parse_args()

    try:
        git = Git()
        if not git.is_clean():
            raise UserError("Error: repo not clean")

        commit = git.commit(args.commit)

        with Continuation.main(git, tool="git-squash"):
            with EditBranch(git, message="git-squash"):
                with edit_commit(commit, git=git):
                    with CatchStop(git):
                        with OrSquash(git, head=commit.sha):
                            if args.fixup:
                                raise Fixup
                            else:
                                raise Squash

    except UserError as e:
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
