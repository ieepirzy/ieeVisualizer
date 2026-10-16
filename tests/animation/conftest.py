"""Animation tests render inline (fast, inspectable); the worker-process
default is exercised by test_anim_isolation.py."""

import os

os.environ.setdefault("IEEVISUALIZER_ANIMATION_ISOLATION", "inline")
