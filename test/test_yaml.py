import yaml
from dataclasses import dataclass
from typing import List, Optional

from gitq.yaml import YAMLObject
from gitq.git import Sha
from gitq.continuations import Loader, Dumper


@dataclass
class WithSha(YAMLObject):
    yaml_loader = Loader
    yaml_dumper = Dumper
    sha: Sha


@dataclass
class WithOptionalSha(YAMLObject):
    yaml_loader = Loader
    yaml_dumper = Dumper
    sha: Sha | None = None


@dataclass
class WithOptionalShaTyping(YAMLObject):
    yaml_loader = Loader
    yaml_dumper = Dumper
    sha: Optional[Sha] = None


@dataclass
class WithShaList(YAMLObject):
    yaml_loader = Loader
    yaml_dumper = Dumper
    shas: List[Sha]


def roundtrip(obj):
    s = yaml.dump(obj, Dumper=Dumper)
    print(s)
    return yaml.load(s, Loader=Loader)


def test_sha_field():
    loaded = roundtrip(WithSha(sha=Sha("abc123")))
    assert isinstance(loaded.sha, Sha)
    assert loaded.sha == "abc123"


def test_optional_sha_present():
    loaded = roundtrip(WithOptionalSha(sha=Sha("abc123")))
    assert isinstance(loaded.sha, Sha)
    assert loaded.sha == "abc123"


def test_optional_sha_absent():
    loaded = roundtrip(WithOptionalSha(sha=None))
    assert loaded.sha is None


def test_optional_sha_typing_present():
    loaded = roundtrip(WithOptionalShaTyping(sha=Sha("abc123")))
    assert isinstance(loaded.sha, Sha)
    assert loaded.sha == "abc123"


def test_optional_sha_typing_absent():
    loaded = roundtrip(WithOptionalShaTyping(sha=None))
    assert loaded.sha is None


def test_sha_list():
    shas = [Sha("abc"), Sha("def"), Sha("a1b2c3")]
    loaded = roundtrip(WithShaList(shas=shas))
    assert all(isinstance(s, Sha) for s in loaded.shas)
    assert loaded.shas == shas


def test_sha_list_empty():
    loaded = roundtrip(WithShaList(shas=[]))
    assert loaded.shas == []
