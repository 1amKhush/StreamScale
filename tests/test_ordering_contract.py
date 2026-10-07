import json
from pathlib import Path

import pytest

from streamscale.contract import timestamp

CASES = json.loads(
    (Path(__file__).parent / "fixtures/projection-cases.v1.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[case["case"] for case in CASES])
def test_projection_contract_examples(case):
    incoming = (timestamp(case["incoming_time"]), case["incoming_offset"])
    stored = (timestamp(case["stored_time"]), case["stored_offset"])
    assert (incoming > stored) is case["apply"]
