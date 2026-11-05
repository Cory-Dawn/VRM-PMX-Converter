# -*- coding: utf-8 -*-
"""双击这个文件（或打包后的 exe）启动转换器。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vrm2pmx import __version__
from vrm2pmx.gui import main


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(__version__)
        sys.exit(0)
    if "--smoke-test" in sys.argv:
        # Packaged-bundle self test: create and destroy the tkinter window
        # without entering the event loop. Exit code 0 means tkinter/numpy/PIL
        # imports and the GUI constructor all work inside the archive.
        from vrm2pmx.gui import App
        app = App()
        app.update_idletasks()
        app.destroy()
        sys.exit(0)
    main()
