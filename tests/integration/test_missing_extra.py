"""A command whose optional extra is not installed stops with the command that installs it."""
import os
import subprocess  # nosec B404
import sys

import pytest

pytestmark = pytest.mark.black_box


def test_optics_mcp_without_the_mcp_extra_names_the_install_command(tmp_path):
    (tmp_path / "fastmcp.py").write_text("raise ImportError('No module named fastmcp', name='fastmcp')\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    result = subprocess.run(  # nosec B603
        [sys.executable, "-m", "optics_framework.helper.cli", "mcp"],
        capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 1
    assert "optics setup --install mcp" in result.stderr
    assert "Unexpected error" not in result.stderr
