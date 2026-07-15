#!/usr/bin/env python3
"""Thin shim: the supervisor moved to optics_framework.helper.supervisor so
`optics supervise` can import it. This path keeps the standalone invocation
(`python tools/supervisor_app/supervisor_tool.py ...`) working."""

from optics_framework.helper.supervisor.supervisor_tool import *  # noqa: F401,F403
from optics_framework.helper.supervisor.supervisor_tool import main

if __name__ == "__main__":
    main()
