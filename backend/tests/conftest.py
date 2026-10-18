from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "sample_submissions"


@pytest.fixture
def samples_dir() -> Path:
    return SAMPLES_DIR
