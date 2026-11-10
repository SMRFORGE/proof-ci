import json
from pathlib import Path

SAMPLE = Path(__file__).parent / "data" / "sample.json"


def test_sample_has_name():
    assert json.loads(SAMPLE.read_text(encoding="utf-8"))["name"] == "demo"
