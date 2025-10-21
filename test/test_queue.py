from textwrap import dedent
from gitq.queue import QueueFile


def test_yaml():

    q = QueueFile.loads("{}")
    assert q.description is None
    assert q.baselines == []

    y = """
        title: my branch
        description: |
          This is a branch.
          Foo Bar Baz
        baselines:
        - sha: xyz
        - sha: abcdef
          ref: bar
          remote: https://example.com/project.git
    """

    q = QueueFile.loads(dedent(y))
    assert q.description == "This is a branch.\nFoo Bar Baz\n"
    foo, bar = q.baselines
    assert foo.ref is None
    assert foo.remote is None
    assert foo.sha == "xyz"
    assert bar.ref == "bar"
    assert bar.remote == "https://example.com/project.git"
    assert bar.sha == "abcdef"

    assert dedent(y).strip() == q.dumps().strip()

    try:
        QueueFile.loads("lol: wtf")
    except Exception:
        pass
    else:
        raise Exception("parse should have failed")
