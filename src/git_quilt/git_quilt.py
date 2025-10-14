import re
import argparse
from typing import Iterator
from pathlib import Path

from .git import Git, Commit, GitFailed, UserError
from .quilt import QuiltFile, Baseline
from .continuations import EditBranch, Continuation, PickCherries


def message(m: str, title: str | None):
    trailers = "Tool: git-quilt"
    if title:
        return f"{m}: {title}\n\n{trailers}"
    else:
        return f"{m}\n\n{trailers}"


def from_this_tool(c: Commit) -> bool:
    return c.message.rstrip().endswith("\nTool: git-quilt")


class Quilt:

    git: Git
    q: QuiltFile

    quiltfile_name = ".git-quilt"

    @property
    def quiltfile_path(self) -> Path:
        return self.git.directory / self.quiltfile_name

    def __init__(self, git: Git):
        self.git = git
        if not self.quiltfile_path.exists():
            raise UserError("This branch is not a quilt.")
        with open(self.quiltfile_path, "r") as f:
            self.q = QuiltFile.load(f)

    def save_quiltfile(self):
        with open(self.quiltfile_path, "w") as f:
            self.q.dump(f)
        self.git("add", self.quiltfile_path)
        self.git("commit", "--amend", "-C", "HEAD")

    def merge_baselines(self) -> Commit:

        baseline, *baselines = self.q.baselines
        assert baseline.sha

        if not baselines:
            self.git.checkout(baseline.sha)
            self.git("commit", "--allow-empty", "-m", message("baseline", self.q.title))
            self.save_quiltfile()
            return self.git.commit("HEAD")

        self.git.checkout(baseline.sha)

        refs = [b.sha for b in baselines]

        try:
            self.git("merge", *refs, "-m", message("merged baselines", self.q.title))
        except GitFailed:
            self.git("merge", "--abort")
        else:
            self.save_quiltfile()
            return self.git.commit("HEAD")

        for ref in refs:
            self.git.cmd(["git", "merge", ref])

        self.save_quiltfile()
        return self.git.commit("HEAD")

    def init(self):
        self.git("add", self.quiltfile_path)
        self.git("commit", "-m", message("initialized quilt", self.q.title))

    def find_patches(self) -> Iterator[Commit]:
        if self.git.on_orphan_branch():
            return
        commits = self.git.commits(*(f"^{b.sha}" for b in self.q.baselines), "HEAD", reverse=True)
        for commit in commits:
            if from_this_tool(commit):
                continue
            changed = self.git("show", "--name-only", "--pretty=", commit.sha).strip()
            if changed == self.quiltfile_name:
                continue
            yield commit

    def rebase(self) -> None:

        if not self.git.is_clean():
            raise UserError("Error: repo not clean")

        patches = list(self.find_patches())
        self.q.baselines = [refresh_baseline(b, git=self.git) for b in self.q.baselines]

        with Continuation.main(self.git, tool="git-quilt"):
            with EditBranch(self.git, message="git-quilt rebase"):
                with PickCherries(self.git, cherries=[b.sha for b in patches]):
                    self.merge_baselines()


def parse_baseline(ref: str, *, git: Git) -> Baseline:
    "create a new basline from user-provided string"
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


def refresh_baseline(baseline: Baseline, *, git: Git) -> Baseline:
    if baseline.ref is None:
        return baseline
    elif baseline.remote:
        if baseline.ref.startswith("refs/heads/") and (remote := git.find_remote(baseline.remote)):
            git.cmd(["git", "fetch", remote])
            branch = baseline.ref.removeprefix("refs/heads/")
            fetched = f"refs/remotes/{remote}/{branch}"
        else:
            git.cmd(["git", "fetch", baseline.remote, baseline.ref])
            fetched = "FETCH_HEAD"
        return Baseline(git.commit(fetched).sha, baseline.ref, baseline.remote)
    else:
        return Baseline(git.commit(baseline.ref).sha, baseline.ref, None)


def main():
    parser = argparse.ArgumentParser("git-quilt", description="manage a bunch of patches")
    subs = parser.add_subparsers(dest="command")

    init_parser = subs.add_parser("init", help="initialize a quilt")
    init_parser.add_argument("baseline", action="extend", nargs="+")
    init_parser.add_argument("--title")

    subs.add_parser("rebase", help="rebase quilt onto baselines")

    subs.add_parser("tidy", help="normalize .git-quilt file")

    args = parser.parse_args()
    if args.command is None:
        parser.print_usage()

    git = Git()
    quiltfile = git.directory / ".git-quilt"

    if args.command == "tidy":
        if quiltfile.exists():
            with open(quiltfile, "r") as f:
                q = QuiltFile.load(f)
            with open(quiltfile, "w") as f:
                q.dump(f)

    if args.command == "init":
        if not git.is_clean():
            raise UserError("Error: repo not clean")
        baselines = [parse_baseline(ref, git=git) for ref in args.baseline]
        q = QuiltFile(baselines=list(baselines), title=args.title)
        with open(quiltfile, "w") as f:
            q.dump(f)
        Quilt(git).init()

    if args.command == "rebase":
        Quilt(git).rebase()


if __name__ == "__main__":
    main()
