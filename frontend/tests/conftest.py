import os
import sys

FRONTEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(FRONTEND), "backend")
for path in (FRONTEND, BACKEND):
    if path not in sys.path:
        sys.path.insert(0, path)
