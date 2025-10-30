from dataclasses import dataclass, field, fields
from typing import List, Self, IO, Iterator
from io import StringIO
from pathlib import Path

import yaml

from .git import Git, Commit, GitFailed, UserError
from .continuations import EditBranch, PickCherries


class YAMLObject(yaml.YAMLObject):

    # Override to_yaml to customize the yaml representation.
    #   * Order of fields is as declared in the dataclass.
    #   * False values are skipped.
    #   * Multiline strings are represented with pipe-style yaml strings.
    @classmethod
    def to_yaml(cls, dumper: yaml.Dumper, data: Self):
        def i():
            for f in fields(cls):  # type: ignore
                value = getattr(data, f.name)
                if not value:
                    continue
                if isinstance(value, str) and "\n" in value:
                    rep = dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
                else:
                    rep = dumper.represent_data(value)
                yield (dumper.represent_data(f.name), rep)

        return yaml.MappingNode(cls.yaml_tag, list(i()))


class Loader(yaml.SafeLoader):

    # By default, PyYAML uses __new__() and .__dict__.update() to construct
    # objects.  Use the constructor provided by dataclasses instead, so that
    # defaults are respected and unknown fields raise exceptions.
    def construct_yaml_object(self, node, cls):
        state = self.construct_mapping(node, deep=True)
        return cls(**state)  # type: ignore


@dataclass
class Baseline(YAMLObject):
    yaml_tag = "!Baseline"
    yaml_loader = Loader
    sha: str
    ref: str | None = field(default=None)
    remote: str | None = field(default=None)


yaml.add_path_resolver("!QueueFile", [], Loader=Loader)
yaml.add_path_resolver("!Baseline", ["baselines", None], Loader=Loader)


@dataclass
class QueueFile(YAMLObject):
    yaml_tag = "!QueueFile"
    yaml_loader = Loader
    title: str | None = field(default=None)
    description: str | None = field(default=None)
    baselines: List[Baseline] = field(default_factory=list)

    def dump(self, f: IO):
        yaml.dump(self, f)

    def dumps(self) -> str:
        with StringIO() as f:
            yaml.dump(self, f)
            return f.getvalue()

    @classmethod
    def load(cls, f: IO) -> Self:
        return yaml.load(f, Loader=Loader)

    @classmethod
    def loads(cls, s: str) -> Self:
        with StringIO(s) as f:
            return yaml.load(f, Loader=Loader)


def message(m: str, title: str | None):
    trailers = "Tool: gitq"
    if title:
        return f"{m}: {title}\n\n{trailers}"
    else:
        return f"{m}\n\n{trailers}"


def from_this_tool(c: Commit) -> bool:
    return c.message.rstrip().endswith("\nTool: gitq")


class Queue:

    git: Git
    q: QueueFile

    queuefile_name = ".git-queue"

    @property
    def queuefile_path(self) -> Path:
        return self.git.directory / self.queuefile_name

    def __init__(self, git: Git):
        self.git = git
        if not self.queuefile_path.exists():
            raise UserError("This branch is not a queue.")
        with open(self.queuefile_path, "r") as f:
            self.q = QueueFile.load(f)

    def save_queuefile(self):
        with open(self.queuefile_path, "w") as f:
            self.q.dump(f)
        self.git("add", self.queuefile_path)
        self.git("commit", "--amend", "-C", "HEAD")

    def merge_baselines(self) -> Commit:

        baseline, *baselines = self.q.baselines
        assert baseline.sha

        if not baselines:
            self.git.checkout(baseline.sha)
            self.git("commit", "--allow-empty", "-m", message("baseline", self.q.title))
            self.save_queuefile()
            return self.git.commit("HEAD")

        self.git.checkout(baseline.sha)

        refs = [b.sha for b in baselines]

        try:
            self.git("merge", *refs, "-m", message("merged baselines", self.q.title))
        except GitFailed:
            self.git("merge", "--abort")
        else:
            self.save_queuefile()
            return self.git.commit("HEAD")

        for ref in refs:
            self.git.cmd(["git", "merge", ref])

        self.save_queuefile()
        return self.git.commit("HEAD")

    def init(self):
        self.git("add", self.queuefile_path)
        self.git("commit", "-m", message("initialized queue", self.q.title))

    def find_patches(self) -> Iterator[Commit]:
        if self.git.on_orphan_branch():
            return
        commits = self.git.commits(*(f"^{b.sha}" for b in self.q.baselines), "HEAD", reverse=True)
        for commit in commits:
            if from_this_tool(commit):
                continue
            if commit.is_merge:
                if self.git.is_conflicted(commit):
                    continue
                else:
                    raise UserError("rebasing merges is not implemented yet")
            changed = self.git("show", "--name-only", "--pretty=", commit.sha).strip()
            if changed == self.queuefile_name:
                continue
            yield commit

    def rebase(self) -> None:
        patches = list(self.find_patches())
        self.q.baselines = [refresh_baseline(b, git=self.git) for b in self.q.baselines]
        with EditBranch(self.git, message="git-queue rebase"):
            with PickCherries(self.git, cherries=[b.sha for b in patches], edit=True):
                self.merge_baselines()


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
