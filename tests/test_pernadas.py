# -*- coding: utf-8 -*-
"""As pernadas: horas de um marco ao outro, a referencia e os degraus."""
import pytest

from app import pernadas, viagens

# Uma viagem inteira, com offset -03:00 em todos os marcos: as DIFERENCAS
# nao dependem do fuso, entao os minutos esperados saem direto do relogio.
VIAGEM = (
    (10, "sailing", "2026-03-01T18:40"),
    (20, "arrival", "2026-03-02T09:15"),
    (20, "sailing", "2026-03-02T10:05"),
    (30, "arrival", "2026-03-03T22:30"),
    (30, "berth", "2026-03-04T06:10"),
    (30, "unberth", "2026-03-05T14:20"),
    (30, "sailing", "2026-03-05T16:05"),
    (40, "arrival", "2026-03-06T20:40"),
    (40, "sailing", "2026-03-06T21:25"),
    (50, "arrival", "2026-03-07T04:10"),
    (50, "sailing", "2026-03-07T11:50"),
    (60, "arrival", "2026-03-07T19:30"),
    (60, "berth", "2026-03-07T23:50"),
    (60, "unberth", "2026-03-08T07:55"),
)


def _abrir(conn):
    viagem_id, erros = viagens.abrir_viagem(conn, 1, por="teste")
    assert not erros
    ids = {r["ordem"]: r["id"] for r in conn.execute(
        "SELECT ordem, id FROM escala WHERE viagem_id = ?", (viagem_id,))}
    return viagem_id, ids


def _lancar(conn, ids, marcos, prefixo="p"):
    for i, (ordem, tipo, hora) in enumerate(marcos):
        _, erros = viagens.lancar_marco(
            conn, ids[ordem], tipo=tipo, hora_local=hora, offset="-03:00",
            nome_responsavel="Cmt.", registrado_por="teste",
            id_cliente="{}-{}".format(prefixo, i))
        assert not erros, (tipo, erros)


def test_cada_pernada_e_a_diferenca_entre_dois_marcos(conn):
    viagem_id, ids = _abrir(conn)
    _lancar(conn, ids, VIAGEM)
    p = pernadas.calcular(conn, viagem_id)

    assert p["sub_nav_alumar_faz"] == 14 * 60 + 35
    assert p["sub_bunker_saida"] == 0            # nao houve: zero, nao "sem dado"
    assert p["sub_espera_faz"] == 50
    assert p["sub_nav_faz_juruti"] == 36 * 60 + 25
    assert p["jur_espera_berco"] == 7 * 60 + 40
    assert p["jur_carregamento"] == 32 * 60 + 10
    assert p["jur_operacao"] == 41 * 60 + 35
    assert p["des_nav_juruti_faz"] == 28 * 60 + 35
    assert p["des_espera_faz"] == 45
    assert p["des_nav_faz_bn"] == 6 * 60 + 45
    assert p["des_espera_mare"] == 7 * 60 + 40
    assert p["des_nav_bn_alumar"] == 7 * 60 + 40
    assert p["des_total"] == 51 * 60 + 25       # a linha grossa do orcamento
    assert p["alu_espera_berco"] == 4 * 60 + 20
    assert p["alu_descarga"] == 8 * 60 + 5
    assert p["alu_operacao"] == 12 * 60 + 25
    assert p["_duracao"] == 6 * 24 * 60 + 13 * 60 + 15


def test_pernada_sem_os_dois_marcos_nao_existe_ainda(conn):
    """None, e nao zero: zero seria uma afirmacao ('levou 0h')."""
    viagem_id, ids = _abrir(conn)
    _lancar(conn, ids, VIAGEM[:4])              # ate o Arrival em Juruti
    p = pernadas.calcular(conn, viagem_id)
    assert p["sub_nav_faz_juruti"] == 36 * 60 + 25
    assert p["jur_espera_berco"] is None
    assert p["des_total"] is None
    assert p["_duracao"] is None
    assert p["_de"]["jur_espera_berco"] is not None   # o Arrival ja esta la
    assert p["_de"]["des_espera_mare"] is None


def test_o_bunker_na_saida_soma_a_parada_adicional(conn):
    viagem_id, ids = _abrir(conn)
    _lancar(conn, ids, VIAGEM[:1])
    extra, erros = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ICOARACI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10, por="teste")
    assert not erros
    for i, (tipo, hora) in enumerate((("arrival", "2026-03-01T22:00"),
                                      ("sailing", "2026-03-02T01:30"))):
        _, erros = viagens.lancar_marco(
            conn, extra, tipo=tipo, hora_local=hora, offset="-03:00",
            nome_responsavel="Cmt.", registrado_por="teste", id_cliente="bk-{}".format(i))
        assert not erros
    assert pernadas.calcular(conn, viagem_id)["sub_bunker_saida"] == 3 * 60 + 30


def test_a_premissa_ganha_da_media():
    historico = [{"jur_espera_berco": 460}, {"jur_espera_berco": 735},
                 {"jur_espera_berco": None}]                 # sem os dois marcos: fora
    assert pernadas.referencia("jur_espera_berco", {}, historico) == (598, "média")
    assert pernadas.referencia("jur_espera_berco", {"jur_espera_berco": 5.5}, historico) \
        == (330, "orçado")
    assert pernadas.referencia("jur_espera_berco", {}, []) == (None, "")


@pytest.mark.parametrize("minutos, base, esperado", [
    (100, 100, 0), (115, 100, 0), (116, 100, 1), (131, 100, 2), (161, 100, 3),
    (None, 100, 0), (100, None, 0),
    # referencia ZERO: degraus em horas absolutas, nao em razao
    (0, 0, 0), (60, 0, 0), (61, 0, 1), (181, 0, 2), (361, 0, 3),
])
def test_os_degraus_do_desvio(minutos, base, esperado):
    assert pernadas.degrau(minutos, base) == esperado


def test_gravar_premissas_insere_atualiza_e_apaga(conn):
    assert pernadas.gravar_premissas(conn, {"sub_nav_alumar_faz": 47.0}, por="vlo") == []
    assert pernadas.premissas(conn) == {"sub_nav_alumar_faz": 47.0}

    pernadas.gravar_premissas(conn, {"sub_nav_alumar_faz": 45.5}, por="vlo")
    assert pernadas.premissas(conn)["sub_nav_alumar_faz"] == 45.5

    pernadas.gravar_premissas(conn, {"sub_nav_alumar_faz": None}, por="vlo")
    assert pernadas.premissas(conn) == {}          # vazio apaga: volta a media

    erros = pernadas.gravar_premissas(conn, {"nao_existe": 1, "jur_operacao": -2}, por="vlo")
    assert len(erros) == 2
    assert pernadas.premissas(conn) == {}


def test_as_chaves_nao_mudam_por_acidente():
    """A premissa cadastrada referencia a chave. Renomear uma orfana o orcamento."""
    assert pernadas.CHAVES == (
        "sub_nav_alumar_faz", "sub_bunker_saida", "sub_espera_faz", "sub_nav_faz_juruti",
        "jur_espera_berco", "jur_carregamento", "jur_operacao",
        "des_nav_juruti_faz", "des_espera_faz", "des_nav_faz_bn", "des_espera_mare",
        "des_nav_bn_alumar", "des_total",
        "alu_espera_berco", "alu_descarga", "alu_operacao")
