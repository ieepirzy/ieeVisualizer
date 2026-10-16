"""An adapter the isolation tests run in the worker (importable by name)."""

import dataclasses
import json
import os
import sys

from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
from ieeVisualizer.render.base import RenderWarning


class Probe(MatplotlibAnimationAdapter):
    """``mode``: "report" (a normal render plus a warning naming the process
    and its environment), "spin" (holds the GIL in one C call for minutes),
    "fail" (writes to stderr and raises), "big" (``size`` bytes of content)."""

    def __init__(self, mode: str, size: int = 0) -> None:
        super().__init__()
        self.mode, self.size = mode, size

    @property
    def name(self) -> str:
        return "probe"

    def render(self, anim, request, *, resolver=None, deadline=None):
        if self.mode == "spin":
            sum(range(10**12))  # no bytecode boundary: no deadline check can run
        if self.mode == "fail":
            print("probe: about to fail", file=sys.stderr)
            raise RuntimeError("probe failed")
        res = super().render(anim, request, resolver=resolver, deadline=deadline)
        if self.mode == "big":
            return dataclasses.replace(res, content=os.urandom(self.size))
        seen = {"pid": os.getpid(), "env": sorted(os.environ), "path": sys.path}
        return dataclasses.replace(res, warnings=[*res.warnings,
                                                  RenderWarning("probe", json.dumps(seen))])


def registry(mode: str, size: int = 0):
    from ieeVisualizer.animation.base import AnimationRegistry

    reg = AnimationRegistry()
    reg.register(Probe(mode, size))
    return reg
