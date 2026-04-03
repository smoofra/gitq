import yaml
from textwrap import dedent
from gitq.continuations import Continuations, Loader, Dumper, EditBranch, PickCherries


def test_yaml_round_trip():
    c = Continuations(
        continuations=[EditBranch(message="test", head="refs/heads/main")],
        tool="rebase",
        status="cherry-picking abc123",
    )
    serialized = yaml.dump(c, Dumper=Dumper)
    y = """
        continuations:
        - !EditBranch
          head: refs/heads/main
          message: test
        tool: rebase
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
        continuations=[PickCherries(cherries=["abc", "def"], edit=False)],
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
