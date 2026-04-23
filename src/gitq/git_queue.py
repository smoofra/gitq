import re
import argparse

import yaml

from .git import Git
from .queue import QueueFile, Baseline, Queue, Loader, Dumper, DETECT
from .continuations import Abort, UserError, Heading
from . import continuations


def parse_baseline(ref: str, *, git: Git) -> Baseline:
    "create a new baseline from user-provided string"
    url = None
    sha = git.rev_parse(ref)
    full_name = git.symbolic_full_name(ref)
    if m := re.match(r"refs/remotes/(\w+)/(.*)", full_name or ""):
        remote, branch = m.groups()
        url = git.cmd(["git", "remote", "get-url", remote], quiet=True).strip()
        return Baseline(sha, f"refs/heads/{branch}", url)
    elif ref == sha or ref == "HEAD":
        return Baseline(sha, None, None)
    else:
        return Baseline(sha, full_name, None)


description_init = """
Initialize a queue on the current branch (or a new branch with -b),
with one or more baselines.  Each BASELINE is a branch, tag, or commit.
"""

description_rebase = """
Rebase the queue onto its baselines, incorporating any upstream changes.
If a baseline branch is itself a queue managed by this tool, it will be
recursively rebased first.

Use --add to incorporate an additional baseline, or --remove to drop one,
at the same time as rebasing.

If conflicts arise during cherry-picking, the operation suspends so the user
can resolve them, then resume with `git queue continue`.
"""


class Main(continuations.Main):

    tool = "git-queue"
    continue_command = "git queue continue"

    def main(self) -> None:
        parser = argparse.ArgumentParser(
            "git-queue",
            description="manage a bunch of patches",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        subs = parser.add_subparsers(dest="command")

        init_parser = subs.add_parser(
            "init",
            help="initialize a queue",
            description=description_init,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        init_parser.add_argument("baselines", action="extend", nargs="+", metavar="BASELINE")
        init_parser.add_argument("--title")
        init_parser.add_argument("--bare", action="store_true")
        init_parser.add_argument("--branch", "-b", help="make a new branch")

        add_parser = subs.add_parser(
            "add",
            help="add a baseline",
            description="Add baselines and rebase.",
        )
        add_parser.add_argument("add", action="extend", nargs="+", metavar="BASELINE")
        add_parser.add_argument(
            "--bare", action="store_true", help="convert to bare branch", default=None
        )
        add_parser.add_argument("--no-bare", action="store_false", dest="bare")

        remove_parser = subs.add_parser(
            "remove", help="remove a baseline", description="Remove baselines and rebase."
        )
        remove_parser.add_argument("remove", action="extend", nargs="+", metavar="BASELINE")
        remove_parser.add_argument(
            "--bare", action="store_true", help="convert to bare branch", default=None
        )
        remove_parser.add_argument("--no-bare", action="store_false", dest="bare")

        rebase_parser = subs.add_parser(
            "rebase",
            description=description_rebase,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help="rebase queue onto baselines",
        )
        rebase_parser.add_argument("--add", metavar="BASELINE", action="append", default=[])
        rebase_parser.add_argument("--remove", metavar="BASELINE", action="append", default=[])
        rebase_parser.add_argument(
            "--bare", action="store_true", help="convert to bare branch", default=None
        )
        rebase_parser.add_argument("--no-bare", action="store_false", dest="bare")

        subs.add_parser(
            "tidy", help="normalize .git-queue file", description="Normalize .git-queue file."
        )

        subs.add_parser(
            "status", help="print status", description="Print status of a suspended operation."
        )
        subs.add_parser(
            "continue",
            help="continue suspended operation",
            description="Continue a suspended operation.",
        )
        subs.add_parser(
            "abort",
            help="abort suspend operation",
            description="Abort a suspended operation and restore previous state.",
        )

        commit_parser = subs.add_parser(
            "commit", help="commit changes to queue to a historiography"
        )
        commit_parser.add_argument("--message", "-m", type=str, default="", help="commit message")
        commit_parser.add_argument(
            "--branch", "-b", type=str, default="", help="historiography branch"
        )

        args = parser.parse_args()
        if args.command is None:
            parser.print_usage()

        if args.command == "status":
            self.status()
            return

        if args.command == "continue":
            self.resume(None)
            return

        if args.command == "abort":
            self.resume(Abort())

        queuefile = self.git.directory / Queue.queuefile_name

        if args.command == "tidy":
            if queuefile.exists():
                with open(queuefile, "r") as f:
                    qf = yaml.load(f, Loader=Loader)
                with open(queuefile, "w") as f:
                    yaml.dump(qf, f, Dumper=Dumper)
            return

        if args.command == "commit":
            if not self.git.is_clean():
                raise UserError("Error: repo not clean")
            q = Queue(self.git, bare=DETECT)
            with Heading("Committing changes to queue"):
                if args.branch:
                    q.commit(message=args.message, meta_branch="refs/heads/" + args.branch)
                else:
                    q.commit(message=args.message, meta_branch=q.historiography_branch())
            return

        with self.setup():

            if args.command == "init":
                baselines = [parse_baseline(ref, git=self.git) for ref in args.baselines]
                qf = QueueFile(baselines=list(baselines), title=args.title)
                if args.bare:
                    if args.branch:
                        branch = args.branch
                    else:
                        branch = self.git.branch()
                        if not branch:
                            raise UserError("HEAD is not on a branch")
                    q = Queue(self.git, qf=qf, bare=branch)
                else:
                    q = Queue(self.git, qf=qf, bare=None)
                q.init(branch=args.branch)

            if args.command in ("rebase", "add", "remove"):
                q = Queue(self.git, bare=DETECT)
                onto = list(q.qf.baselines)

                for baseline in getattr(args, "add", ()):
                    onto.append(parse_baseline(baseline, git=self.git))

                for baseline in getattr(args, "remove", ()):
                    if baseline.startswith("refs/"):
                        ref = baseline
                    else:
                        ref = self.git.symbolic_full_name(baseline)
                    for i, baseline in enumerate(onto):
                        if baseline.ref == ref:
                            break
                    else:
                        raise UserError(f"{ref} not found in baselines")
                    del onto[i]

                q.rebase(onto=onto, to_bare=args.bare)


main = Main()

if __name__ == "__main__":
    main()
