from pathlib import Path

import pytest

from biomorphic.testing import synthetic_session

SAMPLE_PHOTO = Path(__file__).resolve().parent.parent / "samples" / "IMG_2295.jpeg"
SAMPLE_MAP = Path(__file__).resolve().parent.parent / "samples" / "IMG_2298.png"


@pytest.fixture
def synthetic_dir(tmp_path: Path) -> Path:
    return synthetic_session(tmp_path / "session")


@pytest.fixture
def sample_photo() -> Path:
    if not SAMPLE_PHOTO.exists():
        pytest.skip("sample photo missing")
    return SAMPLE_PHOTO


@pytest.fixture
def sample_map() -> Path:
    if not SAMPLE_MAP.exists():
        pytest.skip("sample map missing")
    return SAMPLE_MAP
