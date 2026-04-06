from textwrap import dedent
from io import StringIO

import yaml

from gitq.queue import QueueFile, Loader, Dumper


def test_yaml() -> None:

    qf: QueueFile = yaml.load(StringIO("{}"), Loader=Loader)
    assert qf.description is None
    assert qf.baselines == []

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

    qf = yaml.load(StringIO(dedent(y)), Loader=Loader)
    assert qf.description == "This is a branch.\nFoo Bar Baz\n"
    foo, bar = qf.baselines
    assert foo.ref is None
    assert foo.remote is None
    assert foo.sha == "xyz"
    assert bar.ref == "bar"
    assert bar.remote == "https://example.com/project.git"
    assert bar.sha == "abcdef"

    with StringIO() as s:
        yaml.dump(qf, s, Dumper=Dumper)
        assert dedent(y).strip() == s.getvalue().strip()

    try:
        qf = yaml.load(StringIO("lol: wtf"), Loader=Loader)
    except Exception:
        pass
    else:
        raise Exception("parse should have failed")
