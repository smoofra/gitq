from typing import Self
from dataclasses import fields, is_dataclass, Field

import yaml


class YAMLObjectMetaclass(yaml.YAMLObjectMetaclass):

    def __init__(cls, name, bases, kwds):
        cls.yaml_tag = "!" + name
        kwds["yaml_tag"] = cls.yaml_tag
        super().__init__(name, bases, kwds)


def yaml_excluded_fields(cls) -> set[str]:
    """Return the set of attribute names marked yaml_exclude via dataclasses.field()."""
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
                if value is None:
                    continue
                yield (dumper.represent_data(f.name), represent_value(value, dumper))

        return yaml.MappingNode(cls.yaml_tag, list(i()))


class BaseLoader(yaml.SafeLoader):

    # By default, PyYAML uses __new__() and .__dict__.update() to construct
    # objects.  Use the constructor provided by dataclasses instead, so that
    # defaults are respected and unknown fields raise exceptions.
    def construct_yaml_object(self, node, cls):
        state = self.construct_mapping(node, deep=True)
        return cls(**state)  # type: ignore
