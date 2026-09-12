# -*- coding: utf-8 -*-
"""Bunker: o consumo de cada pernada e ROB de saida - ROB de chegada + abastecido."""
from app import bunker, pernadas, viagens

# A mesma viagem de test_pernadas, agora com o ROB lancado em todo marco.
VIAGEM = (
    (10, "sailing", "2026-03-01T18:40", 1421.5, 112.25),
    (20, "arrival", "2026-03-02T09:15", 1388.0, 110.1),
    (20, "sailing", "2026-03-02T10:05", 1386.2, 110.0),
    (30, "arrival", "2026-03-03T22:30", 1301.75, 99.5),
    (30, "berth", "2026-03-04T06:10", 1284.5, 98.2),
    (30, "unberth", "2026-03-05T14:20", 1276.1, 96.4),
    (30, "sailing", "2026-03-05T16:05", 1274.8, 96.1),
    (40, "arrival", "2026-03-06T20:40", 1208.3, 90.7),
    (40, "sailing", "2026-03-06T21:25", 1206.9, 90.5),
    (50, "arrival", "2026-03-07T04:10", 1190.4, 88.9),
    (50, "sailing", "2026-03-07T11:50", 1188.1, 88.6),
    (60, "arrival", "2026-03-07T19:30", 1171.6, 87.0),
    (60, "berth", "2026-03-07T23:50", 1169.9, 86.7),
    (60, "unberth", "2026-03-08T07:55", 1166.2, 85.9),
)


def _abrir(conn):
    viagem_id, erros = viagens.abrir_viagem(conn, 1, por="teste")
    assert not erros
    ids = {r["ordem"]: r["id"] for r in conn.execute(
        "SELECT ordem, id FROM escala WHERE viagem_id = ?", (viagem_id,))}
    return viagem_id, ids


def _lancar(conn, escala_id, tipo, hora, vlsfo, mgo, id_cliente):
    _, erros = viagens.lancar_marco(
        conn, escala_id, tipo=tipo, hora_local=hora, offset="-03:00",
        nome_responsavel="Cmt.", registrado_por="teste", id_cliente=id_cliente,
        rob_vlsfo=vlsfo, rob_mgo=mgo)
    assert not erros, (tipo, erros)


def _viagem_inteira(conn, marcos=VIAGEM):
    viagem_id, ids = _abrir(conn)
    for i, (ordem, tipo, hora, vl, mg) in enumerate(marcos):
        _lancar(conn, ids[ordem], tipo, hora, vl, mg, "b-{}".format(i))
    return viagem_id, ids


def test_consumo_da_pernada_e_rob_de_saida_menos_rob_de_chegada(conn):
    viagem_id, _ = _viagem_inteira(conn)
    calc = pernadas.calcular(conn, viagem_id)
    c = bunker.consumo_por_pernada(bunker.leituras(conn, 1), calc)

    assert c["sub_nav_alumar_faz"] == {"vlsfo": 33.5, "mgo": 2.15}
    assert c["sub_bunker_saida"] == {"vlsfo": 0.0, "mgo": 0.0}      # nao houve
    assert c["sub_espera_faz"]["vlsfo"] == 1.8
    assert c["sub_nav_faz_juruti"]["vlsfo"] == 84.45
    assert c["jur_espera_berco"]["vlsfo"] == 17.25
    assert c["jur_carregamento"]["vlsfo"] == 8.4
    assert c["jur_operacao"]["vlsfo"] == 26.95
    assert c["des_total"]["vlsfo"] == 103.2
    assert c["alu_operacao"]["vlsfo"] == 5.4


def test_o_abastecido_no_meio_da_pernada_entra_na_conta(conn):
    """Sem somar o que entrou, a pernada com bunker pareceria GERAR combustivel."""
    viagem_id, ids = _abrir(conn)
    _lancar(conn, ids[10], "sailing", "2026-03-01T18:40", 1421.5, 112.25, "k-0")
    extra, erros = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ICOARACI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10, por="teste")
    assert not erros
    _lancar(conn, extra, "arrival", "2026-03-01T22:00", 1400.0, 108.0, "k-1")
    _, erros = viagens.registrar_abastecimento(
        conn, extra, vlsfo=420, mgo=35, nome_responsavel="Cmt.", registrado_por="teste")
    assert not erros
    _lancar(conn, extra, "sailing", "2026-03-02T01:30", 1800.0, 140.0, "k-2")
    _lancar(conn, ids[20], "arrival", "2026-03-02T09:15", 1388.0, 110.1, "k-3")

    c = bunker.consumo_por_pernada(bunker.leituras(conn, 1), pernadas.calcular(conn, viagem_id))
    # na parada de bunker: 1400 + 420 - 1800
    assert c["sub_bunker_saida"] == {"vlsfo": 20.0, "mgo": 3.0}
    # e a navegacao inteira, que contem a parada: 1421,5 + 420 - 1388
    assert c["sub_nav_alumar_faz"]["vlsfo"] == 453.5


def test_pernada_sem_rob_de_chegada_nao_tem_consumo(conn):
    viagem_id, _ = _viagem_inteira(conn, VIAGEM[:4])       # ate o Arrival em Juruti
    c = bunker.consumo_por_pernada(bunker.leituras(conn, 1), pernadas.calcular(conn, viagem_id))
    assert c["sub_nav_faz_juruti"]["vlsfo"] == 84.45
    assert c["jur_espera_berco"] == {"vlsfo": None, "mgo": None}
    assert c["des_total"] == {"vlsfo": None, "mgo": None}


def test_o_resumo_da_viagem(conn):
    viagem_id, _ = _viagem_inteira(conn)
    calc = pernadas.calcular(conn, viagem_id)
    r = bunker.resumo_da_viagem(conn, viagem_id, bunker.leituras(conn, 1), calc)
    assert r["vlsfo"]["rob_inicio"] == 1421.5
    assert r["vlsfo"]["rob_fim"] == 1166.2
    assert r["vlsfo"]["abastecido"] == 0.0
    assert r["vlsfo"]["consumido"] == 255.3
    assert r["vlsfo"]["por_dia"] == 38.96                  # 255,3 t em 6,55 dias
    assert r["mgo"]["consumido"] == 26.35


def test_media_e_degrau_em_toneladas():
    assert bunker.media([33.5, 36.1, None, 31.4]) == 33.667
    assert bunker.media([None, None]) is None
    assert bunker.degrau(33.5, 33.5) == 0
    assert bunker.degrau(40.0, 33.5) == 1                  # 1,19x
    assert bunker.degrau(45.0, 33.5) == 2                  # 1,34x
    assert bunker.degrau(60.0, 33.5) == 3                  # 1,79x
    assert bunker.degrau(5.0, 0.0) == 0                    # sem base, sem alerta
    assert bunker.degrau(None, 33.5) == 0
