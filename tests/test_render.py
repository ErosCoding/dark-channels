import json

import pytest

from dark_channels import shorts, subtitles
from dark_channels.render import MediaError, carregar_midias, midias_por_cena, validar_midia


def _manifest():
    return {
        "scenes": [
            {"index": 0, "narration": "um dois tres quatro cinco seis",
             "start": 0.0, "end": 6.0, "duration": 6.0},
            {"index": 1, "narration": "sete oito nove",
             "start": 6.0, "end": 9.0, "duration": 3.0},
        ]
    }


# --- licenças de mídia: a regra que protege a monetização ---------------------

def test_validar_midia_exige_licenca(tmp_path):
    arquivo = tmp_path / "foto.jpg"
    arquivo.write_bytes(b"x")
    with pytest.raises(MediaError, match="license"):
        validar_midia({"path": str(arquivo), "source": "pexels"}, 0)


def test_validar_midia_exige_fonte(tmp_path):
    arquivo = tmp_path / "foto.jpg"
    arquivo.write_bytes(b"x")
    with pytest.raises(MediaError, match="source"):
        validar_midia({"path": str(arquivo), "license": "Pexels License"}, 0)


def test_validar_midia_recusa_fonte_desconhecida(tmp_path):
    arquivo = tmp_path / "foto.jpg"
    arquivo.write_bytes(b"x")
    with pytest.raises(MediaError, match="não entra"):
        validar_midia(
            {"path": str(arquivo), "source": "youtube-aleatorio",
             "license": "sei la"}, 0,
        )


def test_validar_midia_recusa_arquivo_inexistente():
    with pytest.raises(MediaError, match="não existe"):
        validar_midia(
            {"path": "nao-existe.jpg", "source": "pexels", "license": "Pexels"}, 0
        )


def test_validar_midia_aceita_item_completo(tmp_path):
    arquivo = tmp_path / "foto.jpg"
    arquivo.write_bytes(b"x")
    item = {"path": str(arquivo), "source": "pexels", "license": "Pexels License",
            "credit": "Fulano"}
    assert validar_midia(item, 0) == item


def test_carregar_midias_recusa_manifesto_vazio(tmp_path):
    path = tmp_path / "midias.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(MediaError, match="vazio"):
        carregar_midias(path)


def test_midias_por_cena_respeita_a_cena_indicada(tmp_path):
    arquivo = tmp_path / "a.jpg"
    arquivo.write_bytes(b"x")
    base = {"path": str(arquivo), "source": "propria", "license": "propria"}
    itens = [dict(base, scene=1, credit="um"), dict(base, scene=0, credit="zero")]
    distribuidas = midias_por_cena(itens, 2)
    assert distribuidas[0][0]["credit"] == "zero"
    assert distribuidas[1][0]["credit"] == "um"


def test_midias_por_cena_da_a_volta_quando_faltam_midias(tmp_path):
    arquivo = tmp_path / "a.jpg"
    arquivo.write_bytes(b"x")
    item = {"path": str(arquivo), "source": "propria", "license": "propria"}
    distribuidas = midias_por_cena([item], 3)
    assert all(len(m) == 1 for m in distribuidas)


def test_midias_por_cena_recusa_cena_inexistente(tmp_path):
    arquivo = tmp_path / "a.jpg"
    arquivo.write_bytes(b"x")
    item = {"path": str(arquivo), "source": "propria", "license": "propria", "scene": 9}
    with pytest.raises(MediaError, match="não existe"):
        midias_por_cena([item], 2)


# --- legendas -----------------------------------------------------------------

def test_agrupar_palavras():
    assert subtitles.agrupar_palavras("a b c d e", 2) == ["a b", "c d", "e"]
    assert subtitles.agrupar_palavras("", 3) == []


def test_legendas_cobrem_a_cena_sem_buraco_nem_sobreposicao():
    cena = _manifest()["scenes"][0]
    cues = subtitles.legendas_da_cena(cena, 2)
    assert cues[0]["inicio"] == pytest.approx(0.0)
    assert cues[-1]["fim"] == pytest.approx(6.0)
    for anterior, seguinte in zip(cues, cues[1:]):
        assert seguinte["inicio"] == pytest.approx(anterior["fim"], abs=0.002)


def test_legendas_vertical_usam_grupos_curtos():
    cues = subtitles.construir_cues(
        _manifest(), subtitles.ESTILOS["vertical"]["palavras"]
    )
    assert all(len(c["texto"].split()) <= 3 for c in cues)


def test_tempo_srt():
    assert subtitles._tempo_srt(0) == "00:00:00,000"
    assert subtitles._tempo_srt(3723.5) == "01:02:03,500"


def test_construir_srt_tem_numeracao_e_setas():
    srt = subtitles.construir_srt(subtitles.construir_cues(_manifest(), 3))
    assert srt.startswith("1\n")
    assert "-->" in srt


def test_ass_vertical_fica_longe_dos_botoes_do_aplicativo():
    ass = subtitles.construir_ass(subtitles.construir_cues(_manifest(), 3), "vertical")
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    # Margem de baixo generosa: a legenda não pode cair atrás da interface.
    assert subtitles.ESTILOS["vertical"]["margem_v"] > 300
    assert "Dialogue:" in ass


def test_ass_horizontal_tem_a_resolucao_do_video_longo():
    ass = subtitles.construir_ass(subtitles.construir_cues(_manifest(), 7), "horizontal")
    assert "PlayResX: 1920" in ass


def test_estilo_desconhecido_falha():
    with pytest.raises(ValueError):
        subtitles.construir_ass([], "quadrado")


def test_deslocamento_descarta_legendas_antes_do_corte(tmp_path):
    manifest = tmp_path / "narracao.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    saida = subtitles.gerar(manifest, tmp_path / "l.srt", estilo="vertical",
                            deslocamento=6.0)
    texto = saida.read_text(encoding="utf-8")
    # Só as palavras da cena 1 sobrevivem ao deslocamento.
    assert "sete" in texto
    assert "00:00:00,000" in texto


# --- cortes verticais ---------------------------------------------------------

def test_janela_cobre_as_cenas_pedidas():
    assert shorts.janela(_manifest(), [0, 1]) == (0.0, 9.0)
    assert shorts.janela(_manifest(), [1]) == (6.0, 9.0)


def test_janela_recusa_cena_nao_narrada():
    with pytest.raises(ValueError, match="não foram narradas"):
        shorts.janela(_manifest(), [0, 7])


def test_janela_recusa_lista_vazia():
    with pytest.raises(ValueError, match="scene_indexes"):
        shorts.janela(_manifest(), [])


def test_filtro_vertical_corta_para_9_por_16():
    filtro = shorts.filtro_vertical("cortar", 0.5)
    assert "ih*9/16" in filtro
    assert f"scale={shorts.LARGURA}:{shorts.ALTURA}" in filtro


def test_filtro_enquadrar_nao_perde_nada_do_quadro():
    filtro = shorts.filtro_vertical("enquadrar")
    assert "overlay" in filtro and "gblur" in filtro
    assert "crop=w=" not in filtro


def test_foco_desloca_o_recorte():
    assert "*0.0" in shorts.filtro_vertical("cortar", 0.0)
    assert "*1.0" in shorts.filtro_vertical("cortar", 1.0)


def test_foco_fora_da_faixa_falha():
    with pytest.raises(ValueError):
        shorts.filtro_vertical("cortar", 1.5)


def test_modo_desconhecido_falha():
    with pytest.raises(ValueError):
        shorts.filtro_vertical("diagonal")


def test_slug():
    assert shorts.slug("O mistério do Voo 914!") == "o-misterio-do-voo-914"
    assert shorts.slug("!!!") == "short"


def test_montar_todos_recusa_roteiro_nao_aprovado(tmp_path):
    roteiro = tmp_path / "r.json"
    roteiro.write_text(
        json.dumps({"status": "pending", "script": {"shorts": []}}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="aprovação humana"):
        shorts.montar_todos(roteiro, tmp_path / "n.json", tmp_path / "v.mp4", tmp_path)
