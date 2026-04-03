from typing import Self
from dataclasses import fields

import yaml


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
