import re
import argparse

import yaml

from .git import Git
from .queue import QueueFile, Baseline, Queue, Loader as QueueLoader
from .continuations import Abort, UserError
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


class Main(continuations.Main):

    tool = "git-queue"
    suspend_message = "Suspended! Resolve conflicts and resume with `git queue continue`"

    def main(self) -> None:
        parser = argparse.ArgumentParser("git-queue", description="manage a bunch of patches")
        subs = parser.add_subparsers(dest="command")

        init_parser = subs.add_parser("init", help="initialize a queue")
        init_parser.add_argument("baselines", action="extend", nargs="+", metavar="baseline")
        init_parser.add_argument("--title")
        init_parser.add_argument("--branch", "-b")

        add_parser = subs.add_parser("add", help="add a baseline")
        add_parser.add_argument("add", action="extend", nargs="+", metavar="baseline")

        remove_parser = subs.add_parser("remove", help="remove a baseline")
        remove_parser.add_argument("remove", action="extend", nargs="+", metavar="baseline")

        rebase_parser = subs.add_parser("rebase", help="rebase queue onto baselines")
        rebase_parser.add_argument("--add", metavar="baseline", action="append", default=[])
        rebase_parser.add_argument("--remove", metavar="baseline", action="append", default=[])

        subs.add_parser("tidy", help="normalize .git-queue file")

        subs.add_parser("status")
        subs.add_parser("continue")
        subs.add_parser("abort")

        # TODO add subcommand to add and remove baselines

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
                    q = yaml.load(f, Loader=QueueLoader)
                with open(queuefile, "w") as f:
                    q.dump(f)

        with self.setup():

            if args.command == "init":
                baselines = [parse_baseline(ref, git=self.git) for ref in args.baselines]
                q = QueueFile(baselines=list(baselines), title=args.title)
                queue = Queue(self.git, qf=q)
                if args.branch:
                    queue.init_new_branch(args.branch)
                else:
                    queue.init()

            if args.command in ("rebase", "add", "remove"):
                queue = Queue(self.git)
                onto = list(queue.qf.baselines)

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

                queue.rebase(onto=onto)


main = Main()

if __name__ == "__main__":
    main()
