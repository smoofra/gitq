import json
import subprocess
import sys
from textwrap import dedent

import yaml

from gitq.continuations import Continuations, Loader, Dumper, EditBranch, PickCherries
from gitq.git import Sha


def test_yaml_round_trip():
    c = Continuations(
        continuations=[EditBranch(message="test", head="refs/heads/main")],
        ok_resumes=[],
        tool="rebase",
        status="cherry-picking abc123",
    )
    serialized = yaml.dump(c, Dumper=Dumper)
    y = """
        continuations:
        - !EditBranch
          message: test
          head: refs/heads/main
        tool: rebase
        ok_resumes: []
        status: cherry-picking abc123
    """
    assert serialized.strip() == dedent(y).strip()
    loaded = yaml.load(serialized, Loader=Loader)
    assert loaded.tool == c.tool
    assert loaded.status == c.status
    [continuation] = loaded.continuations
    assert isinstance(continuation, EditBranch)
    assert continuation.message == "test"
    assert continuation.head == "refs/heads/main"


def test_yaml_no_status():
    c = Continuations(
        continuations=[PickCherries(cherries=[Sha("abc"), Sha("def")], edit=False)],
        ok_resumes=[],
        tool="split",
    )
    serialized = yaml.dump(c, Dumper=Dumper)
    loaded = yaml.load(serialized, Loader=Loader)
    assert loaded.tool == "split"
    assert loaded.status is None
    assert "status" not in serialized
    [continuation] = loaded.continuations
    assert isinstance(continuation, PickCherries)
    assert continuation.cherries == ["abc", "def"]
    assert not continuation.edit


# Continuation and other YAMLObject classes register themselves with the
# continuation Loader and Dumper as a side effect of their class definition,
# so a module that defines one has no effect until something imports it.  That
# is why `gitq/__init__.py` imports those modules: any tool may suspend with a
# stack of continuations built by any other module, and the process that
# resumes it has to be able to load every tag in .git/continuation.yaml.
#
# Forgetting one of those imports is invisible until a user walks that exact
# suspend/resume path, so check the invariant directly: importing `gitq` must
# register everything that importing the whole package would.

_REGISTRY_PROBE = """
import importlib
import json
import pkgutil

import gitq
from gitq.continuations import Loader, Dumper


def snapshot():
    tags = {
        tag: getattr(constructor, "__self__", None)
        for tag, constructor in Loader.yaml_constructors.items()
        if isinstance(tag, str) and tag.startswith("!")
    }
    classes = {c for c in Dumper.yaml_representers if isinstance(c, type)}
    return tags, classes


# `import gitq` alone, exactly what a tool gets before it loads a continuation
before_tags, before_classes = snapshot()

for module in pkgutil.iter_modules(gitq.__path__):
    importlib.import_module(f"gitq.{module.name}")

after_tags, after_classes = snapshot()

unregistered = [
    {
        "kind": "loader",
        "name": tag,
        "module": getattr(after_tags[tag], "__module__", "?"),
    }
    for tag in sorted(set(after_tags) - set(before_tags))
] + [
    {"kind": "dumper", "name": c.__name__, "module": c.__module__}
    for c in sorted(after_classes - before_classes, key=lambda c: c.__name__)
]

print(json.dumps(unregistered))
"""


def test_every_continuation_is_registered_by_importing_gitq():
    # in a subprocess, because other tests in this session have already
    # imported the submodules this is trying to catch
    p = subprocess.run([sys.executable, "-c", _REGISTRY_PROBE], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    unregistered = json.loads(p.stdout)

    assert not unregistered, "\n".join(
        ["classes not registered by `import gitq`:"]
        + [f"  {u['name']} ({u['kind']}) from {u['module']}" for u in unregistered]
        + [
            "",
            "Add the missing module(s) to the imports in src/gitq/__init__.py,",
            "or .git/continuation.yaml will fail to load when a user resumes.",
        ]
    )
