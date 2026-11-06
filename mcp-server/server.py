"""Starts the Altocue MCP server from this folder, before or after the altocue command is installed.

Plugins run this with the system python3. It loads only altocue/mcp.py, which needs nothing outside the standard library
and works on Python 3.8 or later. The server then runs the installed altocue command for every edit.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from altocue.mcp import main  # noqa: E402  the path above has to come first

if __name__ == "__main__":
    sys.exit(main())
