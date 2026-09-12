import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "gateway"))

FIXTURES = REPO_ROOT / "fixtures"


def fixture_lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()
