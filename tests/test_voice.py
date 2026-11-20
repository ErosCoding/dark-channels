import json

import pytest

from dark_channels.config import load_channel
from dark_channels.voice import (
    MAX_INPUT_CHARS,
    base_url,
    load_approved_script,
    split_text,
    voice_settings,
)


def test_split_text_keeps_short_text_whole():
    assert split_text("Uma frase curta.") == ["Uma frase curta."]


def test_split_text_empty():
    assert split_text("") == []
    assert split_text("   ") == []


def test_split_text_respects_the_endpoint_limit():
    # /v1/audio/speech recusa input acima de 4096 caracteres.
    texto = ("Esta é uma frase de teste com tamanho razoável. " * 400).strip()
    blocos = split_text(texto)
    assert len(blocos) > 1
    assert all(len(b) <= MAX_INPUT_CHARS for b in blocos)
    assert all(b.strip() for b in blocos)


def test_split_text_breaks_on_sentence_end():
    texto = "Primeira frase. " * 300
    blocos = split_text(texto)
    assert all(b.endswith(".") for b in blocos)


def test_split_text_handles_sentence_longer_than_limit():
    # Frase sem pontuação, maior que o limite: ainda tem que caber.
    texto = "palavra " * 2000
    blocos = split_text(texto)
    assert all(len(b) <= MAX_INPUT_CHARS for b in blocos)
    # Nenhuma palavra perdida no caminho.
    assert sum(len(b.split()) for b in blocos) == 2000


def test_split_text_preserves_all_words():
    texto = " ".join(f"palavra{i}" for i in range(1500))
    blocos = split_text(texto)
    assert " ".join(blocos) == texto


def test_voice_settings_defaults_to_portuguese():
    padrao = voice_settings()
    assert padrao["language"] == "pt"
    assert padrao["engine"] == "omnivoice"


def test_voice_settings_reads_the_channel_config():
    cfg = load_channel("exemplo")
    voz = voice_settings(cfg)
    assert voz["language"] == "pt"
    assert voz["format"] == "wav"
    assert voz["pause_between_scenes_ms"] == 450


def test_base_url_is_local_loopback_by_default(monkeypatch):
    monkeypatch.delenv("VOICESTUDIO_URL", raising=False)
    monkeypatch.delenv("OMNIVOICE_PORT", raising=False)
    assert base_url() == "http://127.0.0.1:3900"


def test_base_url_honours_env(monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_URL", "http://127.0.0.1:9999/")
    assert base_url() == "http://127.0.0.1:9999"


def _write(tmp_path, status):
    path = tmp_path / "roteiro.json"
    path.write_text(
        json.dumps({"status": status, "script": {"scenes": []}}), encoding="utf-8"
    )
    return path


def test_load_approved_script_refuses_pending(tmp_path):
    path = _write(tmp_path, "pending")
    with pytest.raises(RuntimeError, match="approved"):
        load_approved_script(path)


def test_load_approved_script_accepts_approved(tmp_path):
    path = _write(tmp_path, "approved")
    assert load_approved_script(path)["status"] == "approved"
