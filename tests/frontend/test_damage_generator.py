"""Run the real JavaScript generator and editor-apply tests from pytest."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_damage_javascript_behavior():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for damage generator behavior tests")
    result = subprocess.run(
        [node, "--test", "--test-isolation=none",
         "tests/frontend/damage_category.test.cjs",
         "tests/frontend/damage_prefill.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
