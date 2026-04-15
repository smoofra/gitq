import sys
import os
import re
import shlex
from contextlib import contextmanager
from typing import ContextManager, List, Any
from contextvars import ContextVar


class Output:

    indentation: ContextVar[int] = ContextVar(
        "indentation", default=int(os.getenv("GITQ_INDENT", "0"))
    )

    @classmethod
    def indent(cls) -> ContextManager:
        return cls.heading()

    @classmethod
    @contextmanager
    def heading(cls, message: str | None = None):
        n = cls.indentation.get() + 1
        if message is not None:
            print(" " * (n - 1) + "#" * n, message)
            sys.stdout.flush()
        token = cls.indentation.set(n)
        try:
            os.environ["GITQ_INDENT"] = str(n)
            yield
        finally:
            cls.indentation.reset(token)
            n = cls.indentation.get()
            if n == 0:
                del os.environ["GITQ_INDENT"]
            else:
                os.environ["GITQ_INDENT"] = str(n)

    @classmethod
    def log_cmd(cls, cmd: List | str, comment: str = ""):
        def quote(x):
            return shlex.quote(re.sub(r"\n.*", "...", str(x), flags=re.DOTALL))

        if not isinstance(cmd, str):
            cmd = " ".join(map(quote, cmd))
        if comment:
            cmd += "  # " + comment
        print(" " * cls.indentation.get() + "+", cmd)
        sys.stdout.flush()

    @classmethod
    def print(cls, *args: Any) -> None:
        for line in " ".join(map(str, args)).splitlines():
            print(" " * cls.indentation.get() + line)

    @classmethod
    def flush(cls):
        sys.stdout.flush()
        sys.stderr.flush()
