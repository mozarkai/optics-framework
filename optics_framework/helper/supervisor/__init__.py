"""Optics supervisor: a scaled, multi-worker front tier for the optics API.

See docs/usage/supervisor_usage.md and `optics supervise --help`.
"""

from optics_framework.helper.supervisor.supervisor_tool import (
    SupervisorConfig,
    app,
    main,
    run_supervisor,
)

__all__ = ["SupervisorConfig", "app", "main", "run_supervisor"]
