import datetime as dt
import os
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("ASSETS_DIR", "/tmp/creative-intel-test-assets")
os.environ["ANTHROPIC_FALLBACKS"] = "default"

from creative_intel import db as dbmod  # noqa: E402
from creative_intel.collectors import load_manual_file  # noqa: E402
from creative_intel.config import load_competitors, load_taxonomy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "samples" / "demo_creatives.json"


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def session():
    dbmod._engine = None
    dbmod._SessionLocal = None
    engine = dbmod.get_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(engine)
    s = dbmod._SessionLocal()
    dbmod.sync_competitors(s, load_competitors())
    s.commit()
    yield s
    s.close()


@pytest.fixture
def sample_raws():
    return load_manual_file(SAMPLE)


@pytest.fixture
def today():
    return dt.date(2026, 9, 25)
