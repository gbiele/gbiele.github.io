import shutil
import subprocess

import pytest

from site_helpers import REPO_ROOT, SITE_DIR, load_legacy


@pytest.fixture(scope="session")
def site():
    """Render the Quarto website once (repo root, no args) and return _site/."""
    if not (REPO_ROOT / "_quarto.yml").is_file():
        pytest.fail(f"_quarto.yml not found in repo root {REPO_ROOT}: no Quarto project to render")
    quarto = shutil.which("quarto")
    if quarto is None:
        pytest.fail("quarto executable not found on PATH")
    proc = subprocess.run(
        [quarto, "render"], cwd=REPO_ROOT, capture_output=True,
        text=True, encoding="utf8", errors="replace",
    )
    if proc.returncode != 0:
        pytest.fail(
            f"`quarto render` exited with {proc.returncode}\n"
            f"--- stdout (tail) ---\n{proc.stdout[-3000:]}\n"
            f"--- stderr (tail) ---\n{proc.stderr[-3000:]}"
        )
    return SITE_DIR


@pytest.fixture(scope="session")
def legacy():
    return load_legacy()
