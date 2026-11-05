"""Console entry point for the packaged converter."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from vrm2pmx.cli import main

if __name__ == "__main__":
    sys.exit(main())
