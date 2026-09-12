import pytest
from oracle.demo import run_demo


@pytest.mark.asyncio
async def test_complete_demo(tmp_path):
    result = await run_demo(tmp_path / "demo")
    assert result["status"] == "PASS"
    assert result["decisions"] == 4
    assert result["states"] == {"alice": "RUNNING", "bob": "RUNNING", "carol": "RUNNING"}
