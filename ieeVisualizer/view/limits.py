"""Size limits and text checks for ViewSpecs, applied before any layout.

A ViewSpec usually comes from an agent, so its size is not under the
library's control. Every limit below is checked by :func:`.validate.validate_view`
(and therefore by ``render_view``) *before* anything is measured or laid
out, and is reported as a structured error with the JSON pointer where the
budget ran out. The values were chosen so that the largest view that passes
still renders in a few seconds; ``docs/VIEWS.md`` ("Size limits") lists the
timings they were measured with.

Flow, table and text budgets are **per view**, not per component, so many
small components cannot add up to an unbounded render.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from ..spec._common import ptr_join

#: Container nesting (``stack``/``grid``/``card``); the root is depth 1.
MAX_DEPTH = 32
#: View nodes (containers and components) in the whole tree.
MAX_VIEW_NODES = 300
#: Figures placed in the view (each is a full matplotlib render).
MAX_FIGURES = 12
#: Flow nodes, summed over every flow in the view.
MAX_FLOW_NODES = 400
#: Flow edges, summed over every flow in the view.
MAX_FLOW_EDGES = 800
#: Invisible layer slots a layered layout inserts for edges spanning more than
#: one layer (sum of ``span - 1`` over all edges), summed over the view.
MAX_FLOW_DUMMY_NODES = 4000
#: Rows a table draws. A longer table shows the first rows and says
#: "Showing N of M rows"; an explicit ``max_rows`` above this is an error.
MAX_TABLE_ROWS = 300
#: Table cells drawn (shown rows x columns), summed over the view.
MAX_TABLE_CELLS = 1000
#: Longest side of a PNG, in pixels (``height * scale``). Checked after layout,
#: since height depends on wrapping; HTML and SVG have no such limit.
MAX_PNG_SIDE_PX = 32_000
#: Characters in any one string of the view (text, labels, titles, table
#: cells, embedded figure strings, metadata).
MAX_TEXT_CHARS = 10_000
#: Characters of text the layout measures, summed over the view's text,
#: notes, labels and titles (not dataset values).
MAX_TOTAL_TEXT_CHARS = 200_000

# XML 1.0 forbids C0 controls other than tab/LF/CR, lone surrogates and
# U+FFFE/U+FFFF; such a string cannot be written into SVG/HTML at all.
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def bad_char(s: str) -> str | None:
    """A description of the first XML-illegal code point in ``s``, or None."""
    m = _ILLEGAL.search(s)
    if m is None:
        return None
    return f"U+{ord(m.group(0)):04X} at index {m.start()}"


def text_problem(s: str) -> tuple[str, str] | None:
    """``(code, message)`` if ``s`` cannot go into a view, else None."""
    where = bad_char(s)
    if where is not None:
        return ("invalid_text",
                f"the string contains {where}, a control character or lone surrogate that "
                "cannot be written to SVG/HTML; remove it (tab, newline and carriage return "
                "are allowed).")
    if len(s) > MAX_TEXT_CHARS:
        return ("text_too_long",
                f"the string has {len(s)} characters; the limit is {MAX_TEXT_CHARS}. Shorten it "
                "or move the long content out of the view.")
    return None


def scan_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str, str]]:
    """``(pointer, code, message)`` for every bad string (keys included) in a
    JSON-like tree, in document order. Iterative: depth cannot overflow it."""
    stack: list[tuple[str, Any]] = [(path, obj)]
    while stack:
        p, v = stack.pop()
        if isinstance(v, str):
            prob = text_problem(v)
            if prob:
                yield (p, *prob)
        elif isinstance(v, dict):
            kids = []
            for k, x in v.items():
                if isinstance(k, str):
                    prob = text_problem(k)
                    if prob:
                        yield (p, prob[0], "a key of this object: " + prob[1])
                        continue
                kids.append((ptr_join(p, k), x))
            stack.extend(reversed(kids))
        elif isinstance(v, (list, tuple)):
            stack.extend(reversed([(ptr_join(p, i), x) for i, x in enumerate(v)]))
