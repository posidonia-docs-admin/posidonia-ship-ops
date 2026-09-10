"""Condicao da escala, combustivel a bordo e o estoque.

A conta que sustenta tudo:
    consumo(anterior -> atual) = ROB_anterior + abastecido_atual - ROB_atual
"""

import pytest

from app import viagens


def lancar(conn, escala_id, tipo, hora, **kw):
    kw.setdefault("nome_responsavel", "Cmt. Teste")
    kw.setdefault("registrado_por", "navio.pathfinder")
    return viagens.lancar_marco(conn, escala_id, tipo=tipo, hora_local=hora, **kw)


@pytest.fixture()
def viagem(conn, pathfinder):
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    ordens = {linha["ordem"]: linha["id"] for linha in conn.execute(
        "SELECT id, ordem FROM escala WHERE viagem_id = ?", (viagem_id,))}
    return viagem_id, ordens


# ---------------------------------------------------------------------------
# Condicao — o que o navio esta fazendo
# ---------------------------------------------------------------------------

def test_condicao_da_rota_padrao(conn, viagem):
    """Vocabulario da aba T_ESCALAS do MOTOR_FRETE, nao um inventado aqui."""
    viagem_id, _ = viagem
    linhas = conn.execute(
        "SELECT codigo_porto, sentido, condicao FROM escala "
        " WHERE viagem_id = ? ORDER BY ordem", (viagem_id,)).fetchall()
    assert [(l["codigo_porto"], l["sentido"], l["condicao"]) for l in linhas] == [
        ("ALUMAR",      "na",      "ballast"),      # sai vazio de Alumar
        ("FAZENDINHA",  "subida",  "ballast"),      # ainda vazio, subindo
        ("JURUTI",      "subida",  "loading"),      # carregando
        ("FAZENDINHA",  "descida", "laden"),        # ja carregado
        ("BARRA_NORTE", "descida", "laden"),        # carregado, esperando mare
        ("ALUMAR",      "descida", "discharging"),  # descarregando
    ]


def test_condicao_e_motivo_sao_perguntas_diferentes(conn, viagem):
    """Em Barra Norte: condicao laden (carregado), motivo espera_mare (por que parou)."""
    viagem_id, _ = viagem
    barra = conn.execute(
        "SELECT condicao, motivo FROM escala "
        " WHERE viagem_id = ? AND codigo_porto = 'BARRA_NORTE'", (viagem_id,)).fetchone()
    assert barra["condicao"] == "laden"
    assert barra["motivo"] == "espera_mare"


def test_escala_de_bunker_nasce_como_bunkering(conn, viagem):
    viagem_id, _ = viagem
    escala_id, erros = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ICOARACI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10)
    assert erros == []
    assert conn.execute(
        "SELECT condicao FROM escala WHERE id = ?", (escala_id,)
    ).fetchone()[0] == "bunkering"


def test_condicao_invalida_e_recusada(conn, viagem):
    viagem_id, _ = viagem
    escala_id, erros = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ITAQUI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10, condicao="navegando")
    assert escala_id is None
    assert any("Condição inválida" in e for e in erros)


# ---------------------------------------------------------------------------
# Combustivel a bordo
# ---------------------------------------------------------------------------

def test_rob_e_gravado_com_o_marco(conn, viagem):
    _, ordens = viagem
    evento_id, erros = lancar(conn, ordens[30], "arrival", "2026-03-01T07:00",
                              rob_vlsfo="480.5", rob_mgo=42)
    assert erros == []
    linha = conn.execute(
        "SELECT rob_vlsfo, rob_mgo FROM evento WHERE id = ?", (evento_id,)).fetchone()
    assert linha["rob_vlsfo"] == 480.5
    assert linha["rob_mgo"] == 42.0


def test_rob_e_opcional(conn, viagem):
    """Campo vazio nao pode segurar o marco: perder a hora por causa do ROB
    seria trocar o certo pelo util."""
    _, ordens = viagem
    evento_id, erros = lancar(conn, ordens[30], "arrival", "2026-03-01T07:00")
    assert erros == [] and evento_id is not None


def test_rob_invalido_e_recusado(conn, viagem):
    _, ordens = viagem
    evento_id, erros = lancar(conn, ordens[30], "arrival", "2026-03-01T07:00",
                              rob_vlsfo="mais ou menos")
    assert evento_id is None
    assert any("número" in e for e in erros)

    evento_id, erros = lancar(conn, ordens[30], "arrival", "2026-03-01T07:00",
                              rob_mgo=-5)
    assert evento_id is None
    assert any("negativo" in e for e in erros)


def test_virgula_decimal_e_aceita(conn, viagem):
    """O comandante digita 480,5 — nao vamos recusar por causa disso."""
    _, ordens = viagem
    evento_id, _ = lancar(conn, ordens[30], "arrival", "2026-03-01T07:00",
                          rob_vlsfo="480,5")
    assert conn.execute(
        "SELECT rob_vlsfo FROM evento WHERE id = ?", (evento_id,)).fetchone()[0] == 480.5


# ---------------------------------------------------------------------------
# Abastecimento
# ---------------------------------------------------------------------------

def test_abastecimento_gravado_e_substituivel(conn, viagem):
    viagem_id, _ = viagem
    escala_id, _ = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ICOARACI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10)

    ok, erros = viagens.registrar_abastecimento(
        conn, escala_id, vlsfo=220, mgo=None,
        nome_responsavel="Cmt.", registrado_por="navio.pathfinder")
    assert ok and erros == []

    # corrigir substitui, nao duplica
    viagens.registrar_abastecimento(
        conn, escala_id, vlsfo=225.5, mgo=12,
        nome_responsavel="Cmt.", registrado_por="navio.pathfinder")
    linhas = conn.execute(
        "SELECT vlsfo, mgo FROM abastecimento WHERE escala_id = ?", (escala_id,)).fetchall()
    assert len(linhas) == 1
    assert linhas[0]["vlsfo"] == 225.5 and linhas[0]["mgo"] == 12.0


def test_abastecimento_vazio_e_recusado(conn, viagem):
    viagem_id, _ = viagem
    escala_id, _ = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ICOARACI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10)
    ok, erros = viagens.registrar_abastecimento(
        conn, escala_id, vlsfo=None, mgo=None,
        nome_responsavel="Cmt.", registrado_por="navio.pathfinder")
    assert ok is False
    assert any("VLSFO ou de MGO" in e for e in erros)


# ---------------------------------------------------------------------------
# O estoque — a conta do delta
# ---------------------------------------------------------------------------

def test_consumo_entre_leituras_e_com_abastecimento_no_meio(conn, viagem):
    """O caso que o Vinicius descreveu: se tenho 100 e amanheco com 600, so faz
    sentido sabendo quanto abasteci no meio."""
    viagem_id, ordens = viagem

    bunker = conn.execute(
        "SELECT id FROM escala WHERE id = ?",
        (viagens.adicionar_escala_extra(
            conn, viagem_id, codigo_porto="ICOARACI", tipo_escala="fundeio",
            motivo="bunker", apos_ordem=10)[0],)).fetchone()["id"]

    lancar(conn, ordens[10], "sailing", "2026-03-01T10:00", rob_vlsfo=500)
    lancar(conn, bunker, "arrival", "2026-03-02T08:00", rob_vlsfo=480)
    lancar(conn, bunker, "sailing", "2026-03-02T20:00", rob_vlsfo=680)
    viagens.registrar_abastecimento(
        conn, bunker, vlsfo=220, mgo=None,
        nome_responsavel="Cmt.", registrado_por="navio.pathfinder")
    lancar(conn, ordens[20], "arrival", "2026-03-04T06:00", rob_vlsfo=640)

    linhas = conn.execute(
        "SELECT porto, marco, rob_vlsfo, abastecido_vlsfo, consumo_vlsfo "
        "  FROM consumo_combustivel ORDER BY hora_utc").fetchall()

    assert [l["porto"] for l in linhas] == ["Alumar", "Icoaraci", "Icoaraci", "Fazendinha"]
    assert linhas[0]["consumo_vlsfo"] is None          # nao ha leitura anterior
    assert linhas[1]["consumo_vlsfo"] == 20.0          # 500 - 480
    # o abastecimento entra no ULTIMO marco da escala: 480 + 220 - 680
    assert linhas[2]["abastecido_vlsfo"] == 220.0
    assert linhas[2]["consumo_vlsfo"] == 20.0
    assert linhas[3]["consumo_vlsfo"] == 40.0          # 680 - 640


def test_tabela_de_combustivel_traz_porto_data_e_condicao(conn, viagem):
    """As colunas que o Vinicius pediu: porto, data e o que o navio esta fazendo."""
    _, ordens = viagem
    lancar(conn, ordens[30], "berth", "2026-03-05T09:00", rob_vlsfo=430, rob_mgo=38)

    linha = conn.execute("SELECT * FROM combustivel_bordo").fetchone()
    assert linha["porto"] == "Juruti"
    assert linha["condicao"] == "loading"
    assert linha["marco"] == "berth"
    assert linha["hora_local"] == "2026-03-05T09:00"
    assert linha["rob_vlsfo"] == 430.0 and linha["rob_mgo"] == 38.0
    assert linha["navio"] == "AMAZON PATHFINDER"


def test_marco_sem_rob_fica_fora_da_tabela_de_combustivel(conn, viagem):
    _, ordens = viagem
    lancar(conn, ordens[30], "arrival", "2026-03-05T07:00")
    assert conn.execute("SELECT COUNT(*) FROM combustivel_bordo").fetchone()[0] == 0


def test_horas_entre_leituras(conn, viagem):
    _, ordens = viagem
    lancar(conn, ordens[30], "arrival", "2026-03-05T07:00", rob_vlsfo=500)
    lancar(conn, ordens[30], "berth", "2026-03-05T19:30", rob_vlsfo=496)
    horas = [l["horas_desde_a_leitura_anterior"] for l in conn.execute(
        "SELECT horas_desde_a_leitura_anterior FROM consumo_combustivel ORDER BY hora_utc")]
    assert horas[0] is None
    assert horas[1] == 12.5
