"""Median wall time of ``import ieeVisualizer.spec`` in fresh interpreters.

    python tools/import_time.py [runs]

Also asserts the import pulls in neither matplotlib nor sympy.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = (
    "import time, sys; t = time.perf_counter(); import ieeVisualizer.spec; "
    "dt = time.perf_counter() - t; "
    "bad = [m for m in ('matplotlib', 'sympy') if m in sys.modules]; "
    "print(dt, ','.join(bad) or '-', 'pydantic' in sys.modules)"
)


def main() -> None:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    times = []
    for _ in range(runs):
        out = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.split()
        times.append(float(out[0]))
        if out[1] != "-":
            raise SystemExit(f"import ieeVisualizer.spec imported {out[1]}")
    print(f"python {sys.version.split()[0]}: median {statistics.median(times) * 1000:.1f} ms, "
          f"min {min(times) * 1000:.1f} ms over {runs} runs; pydantic imported: {out[2]}")


if __name__ == "__main__":
    main()
