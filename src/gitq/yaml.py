import types
import typing
from typing import Any, Self, Type
from dataclasses import fields, is_dataclass, Field
from contextvars import ContextVar

import yaml

from .git import Sha, Git

# This should only be used to supply the .git property of serializable objects
contextGit: ContextVar[Git] = ContextVar("git")


class YAMLObjectMetaclass(yaml.YAMLObjectMetaclass):

    def __init__(cls, name, bases, kwds):
        cls.yaml_tag = "!" + name
        kwds["yaml_tag"] = cls.yaml_tag
        super().__init__(name, bases, kwds)


def yaml_excluded_fields(cls) -> set[str]:
    "Return the set of attribute names marked yaml_exclude via dataclasses.field()."
    excluded = set()
    for klass in cls.__mro__:
        for attr_name, attr_val in vars(klass).items():
            if isinstance(attr_val, Field) and attr_val.metadata.get("yaml_exclude"):
                excluded.add(attr_name)
    return excluded


def represent_value(value, dumper: yaml.Dumper):
    if isinstance(value, str) and "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_data(value)


class YAMLObject(yaml.YAMLObject, metaclass=YAMLObjectMetaclass):

    # Override to_yaml to customize the yaml representation.
    #   * Order of fields is as declared in the dataclass
    #   * Fields marked yaml_exclude are skipped.
    #   * False values are skipped.
    #   * Multiline strings are represented with pipe-style yaml strings.
    @classmethod
    def to_yaml(cls, dumper: yaml.Dumper, data: Self):
        excluded = yaml_excluded_fields(cls)
        assert is_dataclass(cls), cls.__name__

        def i():
            for f in fields(cls):
                if f.name in excluded:
                    continue
                value = getattr(data, f.name)
                if value is None and f.default is None:
                    continue
                yield (dumper.represent_data(f.name), represent_value(value, dumper))

        return yaml.MappingNode(cls.yaml_tag, list(i()))

    @property
    def git(sef) -> Git:
        return contextGit.get()


yaml.add_representer(
    Sha, lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", str(data))
)


def coerce(value: Any, hint: Type) -> Any:
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Union or isinstance(hint, types.UnionType):
        if [a for a in args if a is not type(None)] == [Sha]:
            return Sha(value)
    elif origin is list and args and isinstance(value, list):
        if args == (Sha,):
            return list(map(Sha, value))
    elif hint == Sha:
        return Sha(value)
    return value


class BaseLoader(yaml.SafeLoader):

    # By default, PyYAML uses __new__() and .__dict__.update() to construct
    # objects.  Use the constructor provided by dataclasses instead, so that
    # defaults are respected and unknown fields raise exceptions.
    def construct_yaml_object(self, node, cls):
        state = self.construct_mapping(node, deep=True)
        if is_dataclass(cls):
            hints = typing.get_type_hints(cls)
            for f in fields(cls):
                if f.name in state and f.name in hints:
                    state[f.name] = coerce(state[f.name], hints[f.name])
        return cls(**state)  # type: ignore
