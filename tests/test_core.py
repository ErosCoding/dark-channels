from datetime import datetime, timedelta, timezone

import pytest

from dark_channels.config import load_channel
from dark_channels.research import outlier_score, parse_duration
from dark_channels.script import Scene, Script, slugify, word_count
from dark_channels.upload import build_body


def test_parse_duration():
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT45S") == 45
    assert parse_duration("PT10M") == 600
    assert parse_duration("P1DT1S") == 86401


def test_outlier_score_floors_small_channels():
    assert outlier_score(50_000, 10_000) == 5.0
    assert outlier_score(5_000, 10) == 5.0  # conta como 1.000 inscritos


def test_slugify():
    assert slugify("O mistério do Voo 914!") == "o-misterio-do-voo-914"


def _script() -> Script:
    return Script(
        topic="t", angle="a", hook="h",
        scenes=[Scene(narration="um dois três", visual="v"), Scene(narration="quatro", visual="v")],
        titles=["Título A", "Título B"], thumbnail_ideas=["x"], description="d",
        tags=["mistério", "novo"], sources=["s"], shorts=[],
    )


def test_word_count():
    assert word_count(_script()) == 4


def test_build_body_scheduled_and_ai_flag():
    cfg = load_channel("exemplo")
    when = datetime.now(timezone(timedelta(hours=-3))) + timedelta(days=1)
    body = build_body(cfg, _script().model_dump(), when, title_index=1)
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == when.isoformat()
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["snippet"]["title"] == "Título B"
    assert body["snippet"]["tags"].count("mistério") == 1


def test_build_body_requires_timezone():
    with pytest.raises(ValueError):
        build_body(load_channel("exemplo"), _script().model_dump(), datetime(2026, 11, 20, 18))
