import json
import shutil
import subprocess

import pytest

from dark_channels.config import CHANNELS_DIR, load_channel
from dark_channels.voice import (
    DEFAULT_VOICE,
    MAX_INPUT_CHARS,
    MOTORES_NAO_COMERCIAIS,
    LicencaNaoComercialError,
    base_url,
    concat_audio,
    concat_with_pauses,
    load_approved_script,
    probe_duration,
    probe_sample_rate,
    resolver_motor,
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


def test_o_padrao_do_codigo_tem_licenca_comercial():
    # O padrão do VoiceStudio é o omnivoice (CC-BY-NC). O padrão do nosso
    # código não pode ser: um canal que não escrever `engine` no config.yaml
    # narraria com licença não comercial sem ninguém perceber.
    assert DEFAULT_VOICE["engine"] not in MOTORES_NAO_COMERCIAIS


def test_voice_settings_reads_the_channel_config():
    cfg = load_channel("exemplo")
    voz = voice_settings(cfg)
    assert voz["language"] == "pt-BR"
    assert voz["format"] == "wav"
    assert voz["pause_between_scenes_ms"] == 450


def _todos_os_canais() -> list[str]:
    return sorted(p.name for p in CHANNELS_DIR.iterdir()
                  if (p / "config.yaml").exists())


def test_existe_ao_menos_um_canal_para_conferir():
    # Protege o teste de guarda abaixo de passar à toa por varrer lista vazia.
    assert _todos_os_canais()


@pytest.mark.parametrize("canal", _todos_os_canais())
def test_todo_canal_narra_em_portugues(canal):
    # O primeiro canal é em português; um idioma errado aqui sai como voz
    # errada no vídeo inteiro, sem erro nenhum no caminho.
    assert voice_settings(load_channel(canal))["language"].lower().startswith("pt")


@pytest.mark.parametrize("canal", _todos_os_canais())
def test_nenhum_canal_usa_motor_de_licenca_nao_comercial(canal):
    voz = voice_settings(load_channel(canal))
    assert voz["engine"] not in MOTORES_NAO_COMERCIAIS, (
        f"O canal '{canal}' está configurado com '{voz['engine']}', cujo "
        "modelo é não comercial. Isso inviabiliza a monetização do canal."
    )


def test_resolver_motor_recusa_licenca_nao_comercial():
    # A última linha de defesa: mesmo que o config.yaml peça omnivoice,
    # a narração falha antes de sair qualquer requisição.
    with pytest.raises(LicencaNaoComercialError, match="não comercial"):
        resolver_motor({"engine": "omnivoice"})


def test_resolver_motor_ignora_caixa_e_espaco():
    with pytest.raises(LicencaNaoComercialError):
        resolver_motor({"engine": "  OmniVoice  "})


def test_resolver_motor_sem_engine_cai_no_padrao_comercial():
    assert resolver_motor({}) == DEFAULT_VOICE["engine"]
    assert resolver_motor(None) == DEFAULT_VOICE["engine"]


def test_resolver_motor_aceita_motor_comercial():
    assert resolver_motor({"engine": "supertonic3"}) == "supertonic3"


def test_synthesize_nao_chama_o_servidor_com_motor_nao_comercial(tmp_path, monkeypatch):
    # Garante que a recusa vem ANTES da requisição, não depois.
    import dark_channels.voice as v

    def _explode(*a, **k):
        raise AssertionError("não podia ter chegado a fazer a requisição")

    monkeypatch.setattr(v, "_request", _explode)
    with pytest.raises(LicencaNaoComercialError):
        v.synthesize("teste", tmp_path / "x.wav", voice={"engine": "omnivoice"})


def test_narrate_script_recusa_motor_nao_comercial(tmp_path, monkeypatch):
    import dark_channels.voice as v

    def _explode(*a, **k):
        raise AssertionError("não podia ter chegado a fazer a requisição")

    monkeypatch.setattr(v, "_request", _explode)
    roteiro = {"scenes": [{"narration": "ola", "visual": "v"}]}
    with pytest.raises(LicencaNaoComercialError):
        v.narrate_script(roteiro, tmp_path, voice={"engine": "omnivoice-gguf"})


def test_base_url_is_local_loopback_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("VOICESTUDIO_URL", raising=False)
    monkeypatch.delenv("OMNIVOICE_PORT", raising=False)
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path))
    assert base_url() == "http://127.0.0.1:3900"


def test_base_url_honours_env(monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_URL", "http://127.0.0.1:9999/")
    assert base_url() == "http://127.0.0.1:9999"


def _publicar(tmp_path, registro):
    (tmp_path / "backend.json").write_text(json.dumps(registro), encoding="utf-8")


def test_base_url_usa_a_porta_que_o_aplicativo_publicou(monkeypatch, tmp_path):
    monkeypatch.delenv("VOICESTUDIO_URL", raising=False)
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path))
    _publicar(tmp_path, {"schema": 1, "url": "http://127.0.0.1:4123"})
    assert base_url() == "http://127.0.0.1:4123"


def test_registro_remoto_e_ignorado(monkeypatch, tmp_path):
    # Um registro apontando para fora da máquina mandaria o roteiro para a rede.
    monkeypatch.delenv("VOICESTUDIO_URL", raising=False)
    monkeypatch.delenv("OMNIVOICE_PORT", raising=False)
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path))
    _publicar(tmp_path, {"schema": 1, "url": "http://servidor-remoto:3900"})
    assert base_url() == "http://127.0.0.1:3900"


def test_registro_com_esquema_desconhecido_e_ignorado(monkeypatch, tmp_path):
    monkeypatch.delenv("VOICESTUDIO_URL", raising=False)
    monkeypatch.delenv("OMNIVOICE_PORT", raising=False)
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path))
    _publicar(tmp_path, {"schema": 99, "url": "http://127.0.0.1:4123"})
    assert base_url() == "http://127.0.0.1:3900"


def _ffmpeg_disponivel() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


precisa_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_disponivel(), reason="ffmpeg/ffprobe não estão no PATH"
)


def _tom(path, segundos, taxa):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:sample_rate={taxa}", "-t", str(segundos),
         "-c:a", "pcm_s16le", "-ac", "1", str(path)],
        check=True,
    )
    return path


@precisa_ffmpeg
def test_pausa_entre_cenas_dura_o_que_foi_pedido_em_44100(tmp_path):
    # O silêncio era gerado fixo em 24 kHz. Com narração de 44,1 kHz (que é o
    # que o Supertonic-3 entrega), o demuxer concat tocava o silêncio na taxa
    # da primeira faixa e uma pausa de 450 ms virava 245 ms — encurtando a
    # narração e fazendo a montagem cortar o fim da fala.
    partes = [_tom(tmp_path / f"p{i}.wav", 1.0, 44100) for i in range(3)]
    saida = tmp_path / "junto.wav"
    concat_with_pauses(partes, saida, 0.45)
    esperado = 3 * 1.0 + 2 * 0.45
    assert probe_duration(saida) == pytest.approx(esperado, abs=0.05)


@precisa_ffmpeg
def test_pausa_entre_cenas_tambem_vale_em_24000(tmp_path):
    partes = [_tom(tmp_path / f"p{i}.wav", 1.0, 24000) for i in range(2)]
    saida = tmp_path / "junto.wav"
    concat_with_pauses(partes, saida, 0.3)
    assert probe_duration(saida) == pytest.approx(2.3, abs=0.05)


@precisa_ffmpeg
def test_concat_preserva_a_taxa_das_partes(tmp_path):
    partes = [_tom(tmp_path / f"p{i}.wav", 0.5, 44100) for i in range(2)]
    saida = tmp_path / "junto.wav"
    concat_audio(partes, saida)
    assert probe_sample_rate(saida) == 44100


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
