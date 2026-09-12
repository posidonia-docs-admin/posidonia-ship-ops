# -*- coding: utf-8 -*-
"""A pernada atual se deduz do ultimo marco; o que ficou para tras e 'em falta'."""
from app import frota


def _bloco(porto, tipo_escala, motivo, condicao, sentido, marcos):
    return {
        "escala": {"porto_nome": porto, "tipo_escala": tipo_escala, "motivo": motivo,
                   "condicao": condicao, "sentido": sentido},
        "marcos": [{"tipo": t, "curto": t.title(),
                    "lancado": ({"hora_local": h, "hora_utc": h + ":00"} if h else None)}
                   for t, h in marcos],
    }


def _viagem(**lancados):
    h = lancados.get
    return [
        _bloco("Alumar", "abertura", "abertura", "ballast", "na",
               [("sailing", h("a_s"))]),
        _bloco("Fazendinha", "passagem", "passagem", "ballast", "subida",
               [("arrival", h("f_a")), ("sailing", h("f_s"))]),
        _bloco("Juruti", "operacional", "carregamento", "loading", "subida",
               [("arrival", h("j_a")), ("berth", h("j_b")), ("unberth", h("j_u")),
                ("sailing", h("j_s"))]),
        _bloco("Barra Norte", "passagem", "espera_mare", "laden", "descida",
               [("arrival", h("b_a")), ("sailing", h("b_s"))]),
        _bloco("Alumar", "encerramento", "descarga", "discharging", "descida",
               [("arrival", h("l_a")), ("berth", h("l_b")), ("unberth", h("l_u"))]),
    ]


def test_viagem_nova_aguarda_a_saida():
    s = frota.pernada_atual(_viagem())
    assert s["titulo"] == "Aguardando saída de Alumar" and s["ultimo"] is None
    assert frota.marcos_pulados(_viagem()) == []


def test_sailing_e_navegar_para_a_proxima_parada():
    s = frota.pernada_atual(_viagem(a_s="2026-03-01T18:40"))
    assert s["titulo"] == "Navegando Alumar → Fazendinha"
    assert s["detalhe"] == "subida" and s["condicao"] == "ballast"
    assert s["ultimo"]["curto"] == "Sailing" and s["ultimo"]["porto"] == "Alumar"


def test_arrival_depende_do_tipo_da_parada():
    assert frota.pernada_atual(_viagem(a_s="x", f_a="y"))["titulo"] == "Em Fazendinha"
    assert frota.pernada_atual(_viagem(a_s="x", j_a="y"))["titulo"] == "Aguardando berço em Juruti"
    fundeado = frota.pernada_atual(_viagem(a_s="x", b_a="y"))
    assert fundeado["titulo"] == "Fundeado em Barra Norte"
    assert "mar" in fundeado["detalhe"].lower()


def test_berth_e_unberth():
    atracado = frota.pernada_atual(_viagem(a_s="x", j_a="y", j_b="z"))
    assert atracado["titulo"] == "Atracado em Juruti" and atracado["detalhe"] == "carregando"
    descarga = frota.pernada_atual(_viagem(a_s="x", l_a="y", l_b="z"))
    assert descarga["detalhe"] == "descarregando"
    solto = frota.pernada_atual(_viagem(a_s="x", j_a="y", j_b="z", j_u="w"))
    assert solto["titulo"] == "Desatracado em Juruti" and solto["detalhe"] == "aguardando saída"


def test_marcos_pulados_sao_so_os_que_ficaram_para_tras():
    """Pular Fazendinha e chegar em Juruti: dois em falta. Os de Juruti que
    ainda vao acontecer nao contam — sao espera, nao cobranca."""
    v = _viagem(a_s="x", j_a="y")
    assert frota.marcos_pulados(v) == ["Arrival · Fazendinha", "Sailing · Fazendinha"]
    # a posicao segue a ORDEM da viagem, nao a hora: o navio esta em Juruti
    assert frota.pernada_atual(v)["titulo"] == "Aguardando berço em Juruti"
