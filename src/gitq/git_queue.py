import re
import argparse

import yaml

from .git import Git, GitFailed
from .queue import (
    QueueFile,
    Baseline,
    Queue,
    Loader,
    Dumper,
    DETECT,
    message,
    RebaseOptions,
    NotAQueue,
)
from .output import Output
from .continuations import Abort, UserError, Heading, Skip, SavePatch
from . import continuations


def parse_baseline(ref: str, *, git: Git) -> Baseline:
    "create a new baseline from user-provided string"
    url = None
    sha = git.sha(ref)
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
        rebase_parser.add_argument(
            "--no-refresh",
            action="store_false",
            help="do not refresh baselines",
            dest="refresh",
            default=True,
        )
        rebase_parser.add_argument(
            "--with",
            action="append",
            metavar="MERGE",
            help="provide a user merge",
            dest="user_merges",
        )
        rebase_parser.add_argument(
            "--use-local",
            action="store_true",
            default=None,
            help="use local branches tracking baseline if one exists",
        )
        rebase_parser.add_argument(
            "--no-use-local", action="store_false", default=None, dest="use_local"
        )

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
            "skip",
            help="skip the current commit",
            description="Skip the current commit and continue.",
        )
        subs.add_parser(
            "save-patch",
            help="save the conflicting commit as a patch and continue the rebase",
            description="Abort the current cherry-pick, save the commit as a .patch file "
            "at the repo root, record it in .git-queue, and continue rebasing.",
        )
        apply_parser = subs.add_parser(
            "apply",
            help="apply an unapplied patch",
        )
        apply_parser.add_argument("patch", nargs="?")
        subs.add_parser(
            "abort",
            help="abort suspend operation",
            description="Abort a suspended operation and restore previous state.",
        )

        edit_parser = subs.add_parser(
            "edit",
            help="edit HEAD (a historiography), by checking out a queue branch.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Edit HEAD by creating or checking out the queue branch\n"
            "associated with it.  HEAD should be a historiography branch.",
        )
        edit_parser.add_argument("--branch", "-b", help="branch name")

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

        if args.command == "skip":
            self.resume(Skip())

        if args.command == "save-patch":
            self.resume(SavePatch())

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
                    if branch := self.git.branch():
                        meta_branch = Queue.get_historiography_branch(branch, git=self.git)
                        if meta_branch is None:
                            Queue.set_historiography_branch(branch, args.branch, git=self.git)
                    q.commit(message=args.message, meta_branch="refs/heads/" + args.branch)
                else:
                    q.commit(message=args.message, meta_branch=q.historiography_branch())
            return

        if args.command == "edit":
            if not self.git.is_clean():
                raise UserError("Error: repo not clean")
            q = Queue(self.git, bare=None)
            if not q.is_historiography:
                raise UserError("HEAD is not a historiography branch")
            current_branch = self.git.branch()
            if current_branch is None:
                raise UserError("HEAD is not on a branch")
            if args.branch:
                queue_branch = args.branch
            else:
                queue_branch = Queue.find_queue_branch(current_branch, git=self.git)
                if queue_branch is None:
                    raise UserError(
                        f"No queue branch found for {current_branch}. "
                        f"Use --branch to specify one."
                    )
            sha = q.recreate_queue()
            if self.git.branch_exists(queue_branch):
                if self.git.sha("refs/heads/" + queue_branch) != sha:
                    raise UserError(f"{queue_branch} has diverged from {current_branch}")
            else:
                self.git.cmd(["git", "update-ref", f"refs/heads/{queue_branch}", sha])
                Queue.set_historiography_branch(of=queue_branch, to=current_branch, git=self.git)
            self.git.checkout(queue_branch, comment=f"checking out queue branch {queue_branch}")
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

            if args.command == "apply":
                q = Queue(self.git, bare=DETECT)
                q.apply_patch(args.patch)
                return

            if args.command in ("rebase", "add", "remove"):
                q = Queue(self.git, bare=DETECT)
                onto = list(q.qf.baselines)

                force = not getattr(args, "refresh", True)

                for baseline in getattr(args, "add", ()):
                    force = True
                    onto.append(parse_baseline(baseline, git=self.git))

                for baseline_arg in getattr(args, "remove", ()):
                    force = True
                    try:
                        baseline = parse_baseline(baseline_arg, git=self.git)
                        for i, b in enumerate(onto):
                            if baseline.ref == b.ref and baseline.remote == b.remote:
                                break
                        else:
                            raise UserError(f"{baseline_arg} not found in baselines")
                        del onto[i]
                    except GitFailed:
                        # maybe the ref no longe exists, try to guess
                        found = list()
                        remote = None
                        if m := re.match("(?:(?:refs/)?remotes/)?([^/]+)/(.*)$", baseline_arg):
                            try:
                                remote = self.git.remote_url(m.group(1))
                            except GitFailed:
                                pass
                        for i, b in enumerate(onto):
                            if (
                                b.ref == baseline_arg
                                or b.ref == "refs/heads/" + baseline_arg
                                or (
                                    remote
                                    and m
                                    and b.remote == remote
                                    and b.ref == "refs/heads/" + m.group(2)
                                )
                            ):
                                found.append(i)
                        if not found:
                            raise UserError(f"{baseline_arg} not found in baselines")
                        if len(found) > 1:
                            raise UserError(f"{baseline_arg} is ambiguous")
                        del onto[found[0]]

                opts = RebaseOptions(
                    onto=onto,
                    force=force,
                    to_bare=args.bare,
                    refresh=getattr(args, "refresh", True),
                    user_merges=getattr(args, "user_merges", []) or [],
                )
                if (ul := getattr(args, "use_local", None)) is not None:
                    opts.use_local = ul
                q.rebase(opts)

    def check_clean(self):
        if set(self.git.dirty_files()) == {Queue.queuefile_name}:
            m = message("update .git-queue", "update-queuefile")
            self.git("commit", "-m", m, Queue.queuefile_name)
        super().check_clean()

    def status(self):
        try:
            q = Queue(self.git, bare=DETECT)
        except NotAQueue:
            pass
        else:
            Output.print(self.git.branch() or self.git.head(), "is a queue.")
            Output.print()
            Output.print("baselines:")
            for baseline in q.qf.baselines:
                Output.print("  ", baseline.summary)
            Output.print()
            if q.qf.unapplied_patches:
                Output.print("unapplied patches:")
                for patch in q.qf.unapplied_patches:
                    Output.print("  ", patch)
                Output.print()

        super().status()


main = Main()

if __name__ == "__main__":
    main()
