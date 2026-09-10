"""Lancamento, correcao e as duracoes que a apuracao passa a ter."""

import pytest

from app import dominio, viagens


@pytest.fixture()
def viagem(conn, pathfinder):
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    escalas = {}
    for linha in conn.execute(
            "SELECT id, ordem, codigo_porto, tipo_escala FROM escala "
            " WHERE viagem_id = ? ORDER BY ordem", (viagem_id,)):
        escalas[linha[1]] = linha[0]
    return viagem_id, escalas


def lancar(conn, escala_id, tipo, hora, **kw):
    kw.setdefault("nome_responsavel", "Cmt. Teste")
    kw.setdefault("registrado_por", "navio.pathfinder")
    return viagens.lancar_marco(conn, escala_id, tipo=tipo, hora_local=hora, **kw)


# ---------------------------------------------------------------------------
# Fuso: exigido, nao inferido
# ---------------------------------------------------------------------------

def test_hora_local_mais_fuso_vira_utc():
    assert dominio.para_utc("2026-03-01T14:30", "-03:00") == "2026-03-01T17:30:00Z"


def test_offset_do_porto_entra_pre_preenchido(conn, viagem):
    _, escalas = viagem
    evento_id, erros = lancar(conn, escalas[30], "arrival", "2026-03-01T07:00")
    assert erros == []
    linha = conn.execute(
        "SELECT offset_utc, hora_utc FROM evento WHERE id = ?", (evento_id,)).fetchone()
    assert linha[0] == "-03:00"
    assert linha[1] == "2026-03-01T10:00:00Z"


# ---------------------------------------------------------------------------
# O tipo da escala decide o formulario
# ---------------------------------------------------------------------------

def test_escala_de_passagem_nao_aceita_berth(conn, viagem):
    """Cobrar berth de quem nao atracou e pedir dado que nao existe."""
    _, escalas = viagem
    evento_id, erros = lancar(conn, escalas[50], "berth", "2026-03-01T07:00")
    assert evento_id is None
    assert any("não atraca aqui" in e for e in erros)


def test_escala_operacional_aceita_os_quatro_marcos(conn, viagem):
    _, escalas = viagem
    for i, marco in enumerate(("arrival", "berth", "unberth", "sailing")):
        evento_id, erros = lancar(
            conn, escalas[30], marco, "2026-03-0{}T08:00".format(i + 1))
        assert erros == [], (marco, erros)
        assert evento_id is not None


# ---------------------------------------------------------------------------
# Ordem cronologica e horario no futuro
# ---------------------------------------------------------------------------

def test_berth_nao_pode_ser_anterior_ao_arrival(conn, viagem):
    _, escalas = viagem
    lancar(conn, escalas[30], "arrival", "2026-03-02T10:00")
    evento_id, erros = lancar(conn, escalas[30], "berth", "2026-03-01T10:00")
    assert evento_id is None
    assert any("não pode ser anterior" in e for e in erros)


def test_arrival_nao_pode_ser_posterior_ao_sailing(conn, viagem):
    _, escalas = viagem
    lancar(conn, escalas[50], "sailing", "2026-03-01T10:00")
    evento_id, erros = lancar(conn, escalas[50], "arrival", "2026-03-05T10:00")
    assert evento_id is None
    assert any("não pode ser posterior" in e for e in erros)


def test_marco_no_futuro_e_recusado(conn, viagem):
    _, escalas = viagem
    evento_id, erros = lancar(conn, escalas[30], "arrival", "2035-01-01T10:00")
    assert evento_id is None
    assert any("futuro" in e for e in erros)


def test_erros_saem_todos_de_uma_vez(conn, viagem):
    """POST-Redirect-GET mostrando um erro por vez faz o usuario desistir."""
    _, escalas = viagem
    evento_id, erros = lancar(
        conn, escalas[50], "berth", "2035-01-01T10:00", nome_responsavel="  ")
    assert evento_id is None
    assert len(erros) >= 3  # marco invalido + sem responsavel + futuro


# ---------------------------------------------------------------------------
# Correcao gera versao, nunca sobrescrita
# ---------------------------------------------------------------------------

def test_correcao_sem_motivo_e_recusada(conn, viagem):
    _, escalas = viagem
    lancar(conn, escalas[30], "arrival", "2026-03-01T10:00")
    evento_id, erros = lancar(conn, escalas[30], "arrival", "2026-03-01T10:45")
    assert evento_id is None
    assert any("motivo da correção" in e for e in erros)


def test_correcao_preserva_a_versao_anterior(conn, viagem):
    _, escalas = viagem
    primeiro, _ = lancar(conn, escalas[30], "arrival", "2026-03-01T10:00")
    segundo, erros = lancar(
        conn, escalas[30], "arrival", "2026-03-01T10:45",
        motivo_correcao="Conferido no diario de bordo")
    assert erros == []

    linhas = conn.execute(
        "SELECT id, versao, vigente, hora_local, motivo_correcao, substitui_evento_id "
        "  FROM evento WHERE escala_id = ? AND tipo = 'arrival' ORDER BY versao",
        (escalas[30],)).fetchall()

    assert len(linhas) == 2                      # a anterior continua la
    assert linhas[0]["vigente"] == 0
    assert linhas[0]["hora_local"] == "2026-03-01T10:00"
    assert linhas[1]["vigente"] == 1
    assert linhas[1]["versao"] == 2
    assert linhas[1]["substitui_evento_id"] == primeiro
    assert linhas[1]["motivo_correcao"] == "Conferido no diario de bordo"
    assert segundo == linhas[1]["id"]


def test_so_um_marco_vigente_por_tipo(conn, viagem):
    _, escalas = viagem
    lancar(conn, escalas[30], "arrival", "2026-03-01T10:00")
    lancar(conn, escalas[30], "arrival", "2026-03-01T10:45", motivo_correcao="ajuste")
    vigentes = conn.execute(
        "SELECT COUNT(*) FROM evento_vigente WHERE escala_id = ? AND tipo = 'arrival'",
        (escalas[30],)).fetchone()[0]
    assert vigentes == 1


# ---------------------------------------------------------------------------
# Fila local: reenvio tem de ser inofensivo
# ---------------------------------------------------------------------------

def test_reenvio_do_mesmo_id_de_cliente_nao_duplica(conn, viagem):
    """O celular repete o envio quando o servidor demora a acordar."""
    _, escalas = viagem
    primeiro, erros1 = lancar(
        conn, escalas[30], "arrival", "2026-03-01T10:00", id_cliente="abc-123")
    segundo, erros2 = lancar(
        conn, escalas[30], "arrival", "2026-03-01T10:00", id_cliente="abc-123")

    assert erros1 == [] and erros2 == []
    assert primeiro == segundo
    assert conn.execute(
        "SELECT COUNT(*) FROM evento WHERE escala_id = ?", (escalas[30],)
    ).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# O que as views passam a entregar
# ---------------------------------------------------------------------------

def test_duracoes_da_escala(conn, viagem):
    _, escalas = viagem
    juruti = escalas[30]
    lancar(conn, juruti, "arrival", "2026-03-01T07:00")   # 10:00Z
    lancar(conn, juruti, "berth",   "2026-03-01T13:00")   # 16:00Z  -> 6h de espera
    lancar(conn, juruti, "unberth", "2026-03-02T13:00")   # 24h atracado
    lancar(conn, juruti, "sailing", "2026-03-02T16:00")   # 3h pos-operacao

    linha = conn.execute(
        "SELECT horas_espera_berco, horas_atracado, horas_pos_operacao, horas_total_escala "
        "  FROM escala_completa WHERE escala_id = ?", (juruti,)).fetchone()
    assert linha["horas_espera_berco"] == 6.0
    assert linha["horas_atracado"] == 24.0
    assert linha["horas_pos_operacao"] == 3.0
    assert linha["horas_total_escala"] == 33.0


def test_espera_de_mare_em_barra_norte(conn, viagem):
    """A pergunta que hoje nao tem de onde sair."""
    _, escalas = viagem
    barra = escalas[50]
    lancar(conn, barra, "arrival", "2026-03-03T02:00")
    lancar(conn, barra, "sailing", "2026-03-03T09:30")

    horas = conn.execute(
        "SELECT horas_total_escala FROM escala_completa WHERE escala_id = ?",
        (barra,)).fetchone()[0]
    assert horas == 7.5


def test_fila_de_pendencias_nao_cobra_berth_de_escala_de_passagem(conn, viagem):
    viagem_id, escalas = viagem
    faltantes = conn.execute(
        "SELECT marco_faltante FROM escalas_incompletas WHERE escala_id = ?",
        (escalas[50],)).fetchall()
    assert {r[0] for r in faltantes} == {"arrival", "sailing"}


def test_fila_de_pendencias_esvazia_conforme_o_comandante_lanca(conn, viagem):
    viagem_id, escalas = viagem
    antes = conn.execute(
        "SELECT COUNT(*) FROM escalas_incompletas WHERE escala_id = ?",
        (escalas[50],)).fetchone()[0]
    lancar(conn, escalas[50], "arrival", "2026-03-03T02:00")
    depois = conn.execute(
        "SELECT COUNT(*) FROM escalas_incompletas WHERE escala_id = ?",
        (escalas[50],)).fetchone()[0]
    assert antes == 2 and depois == 1


def test_marco_lancado_entra_na_fila_da_supervisao(conn, viagem):
    _, escalas = viagem
    lancar(conn, escalas[30], "arrival", "2026-03-01T07:00")
    fila = conn.execute("SELECT tipo, nome_responsavel FROM escalas_a_conferir").fetchall()
    assert len(fila) == 1
    assert fila[0]["tipo"] == "arrival"
    assert fila[0]["nome_responsavel"] == "Cmt. Teste"


def test_conferencia_tira_o_marco_da_fila(conn, viagem):
    from app.db import agora

    _, escalas = viagem
    evento_id, _ = lancar(conn, escalas[30], "arrival", "2026-03-01T07:00")
    conn.execute(
        "INSERT INTO conta (login, nome_exibicao, perfil, senha_hash, criada_em) "
        "VALUES ('vlo', 'Vinicius', 'supervisor', 'x', ?)", (agora(),))
    conn.execute(
        "INSERT INTO conferencia (evento_id, conferido_por, conferido_em, resultado) "
        "VALUES (?, 'vlo', ?, 'conferido')", (evento_id, agora()))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM escalas_a_conferir").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Rastreabilidade individual, que a conta por navio nao da sozinha
# ---------------------------------------------------------------------------

def test_nome_de_quem_preencheu_e_obrigatorio(conn, viagem):
    _, escalas = viagem
    evento_id, erros = lancar(
        conn, escalas[30], "arrival", "2026-03-01T07:00", nome_responsavel="   ")
    assert evento_id is None
    assert any("quem preencheu" in e for e in erros)
