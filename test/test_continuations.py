import yaml
from textwrap import dedent
from gitq.continuations import Continuations, Loader, Dumper


def test_yaml_round_trip():
    c = Continuations(
        continuations=[["EditBranch", {"message": "test", "head": "refs/heads/main"}]],
        tool="rebase",
        status="cherry-picking abc123",
    )
    serialized = yaml.dump(c, Dumper=Dumper)
    y = """
        continuations:
        - - EditBranch
          - head: refs/heads/main
            message: test
        tool: rebase
        status: cherry-picking abc123
    """
    assert serialized.strip() == dedent(y).strip()
    loaded = yaml.load(serialized, Loader=Loader)
    assert loaded.tool == c.tool
    assert loaded.status == c.status
    assert loaded.continuations == c.continuations


def test_yaml_no_status():
    c = Continuations(
        continuations=[["PickCherries", {"cherries": ["abc", "def"], "edit": False}]],
        tool="split",
    )
    serialized = yaml.dump(c, Dumper=Dumper)
    loaded = yaml.load(serialized, Loader=Loader)
    assert loaded.tool == "split"
    assert loaded.status is None
    assert loaded.continuations == c.continuations
    assert "status" not in serialized
