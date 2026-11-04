"""Keeps the README's pinned install examples in step with the package version.

PyPI renders README.md as the project page when a version is published, and that
page can't be edited afterwards. If a release bumps pyproject.toml but not the
README, the new PyPI page shows the old version (this happened with 0.3.0).
"""

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
README = REPO_ROOT / "README.md"

PIN = re.compile(r"opsinsight-mcp==(\d+\.\d+\.\d+)")
DOCTOR_BANNER = re.compile(r"opsinsight-mcp doctor \(v(\d+\.\d+\.\d+)\)")


@pytest.mark.skipif(not README.exists(), reason="README only exists in a source checkout")
def test_readme_versions_match_pyproject():
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    readme = README.read_text(encoding="utf-8")

    found = PIN.findall(readme) + DOCTOR_BANNER.findall(readme)
    assert found, "README has no pinned opsinsight-mcp examples; update this test"

    stale = sorted({v for v in found if v != version})
    assert not stale, (
        f"README.md mentions opsinsight-mcp {stale} but pyproject.toml is {version}. "
        f"Update every README example to {version} before tagging the release."
    )
