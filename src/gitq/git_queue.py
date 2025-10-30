import re
import argparse

from .git import Git
from .queue import QueueFile, Baseline, Queue
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
        init_parser.add_argument("baseline", action="extend", nargs="+")
        init_parser.add_argument("--title")

        subs.add_parser("rebase", help="rebase queue onto baselines")
        subs.add_parser("tidy", help="normalize .git-queue file")
        subs.add_parser("status")
        subs.add_parser("continue")

        args = parser.parse_args()
        if args.command is None:
            parser.print_usage()

        if args.command == "status":
            self.status()
            return

        if args.command == "continue":
            self.resume(None)
            return

        queuefile = self.git.directory / Queue.queuefile_name

        if args.command == "tidy":
            if queuefile.exists():
                with open(queuefile, "r") as f:
                    q = QueueFile.load(f)
                with open(queuefile, "w") as f:
                    q.dump(f)

        with self.setup():

            if args.command == "init":
                baselines = [parse_baseline(ref, git=self.git) for ref in args.baseline]
                q = QueueFile(baselines=list(baselines), title=args.title)
                with open(queuefile, "w") as f:
                    q.dump(f)
                Queue(self.git).init()

            if args.command == "rebase":
                Queue(self.git).rebase()


main = Main()

if __name__ == "__main__":
    main()
