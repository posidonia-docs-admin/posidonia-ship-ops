"""O schema e o seed sobem, e as views aguentam banco vazio."""


def test_seed_traz_os_quatro_amazon(conn):
    nomes = [r[0] for r in conn.execute(
        "SELECT nome_oficial FROM navio WHERE ativo = 1 ORDER BY id")]
    assert nomes == [
        "AMAZON PATHFINDER", "AMAZON PIONEER", "AMAZON COMMANDER", "AMAZON COURAGE",
    ]


def test_seed_traz_os_portos_do_circuito(conn):
    codigos = {r[0] for r in conn.execute("SELECT codigo FROM porto")}
    assert codigos == {
        "ALUMAR", "JURUTI", "FAZENDINHA", "BARRA_NORTE", "ICOARACI", "ITAQUI",
    }


def test_imo_e_locode_ficam_nulos_ate_a_operacao_confirmar(conn):
    """Numero inventado parece certo, e por isso e pior que numero ausente."""
    assert conn.execute("SELECT COUNT(*) FROM navio WHERE imo IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM porto WHERE un_locode IS NOT NULL").fetchone()[0] == 0


def test_alias_de_porto_resolve_grafia_divergente(conn):
    for alias, esperado in (("ITQ", "ITAQUI"), ("IQI", "ITAQUI"),
                            ("BARRA NORTE", "BARRA_NORTE")):
        achado = conn.execute(
            "SELECT codigo_porto FROM porto_alias WHERE alias = ?", (alias,)
        ).fetchone()
        assert achado is not None and achado[0] == esperado


def test_rota_padrao_tem_seis_etapas_na_ordem_certa(conn):
    """A viagem COMECA no Sailing de Alumar e TERMINA no Unberth de Alumar.

    O Sailing e o PRIMEIRO lancamento da viagem — nunca fica preso na anterior.
    """
    etapas = conn.execute(
        "SELECT ordem, codigo_porto, tipo_escala, sentido, motivo "
        "  FROM rota_etapa WHERE rota_modelo_id = 1 ORDER BY ordem"
    ).fetchall()
    assert [tuple(e) for e in etapas] == [
        (1, "ALUMAR",      "abertura",     "na",      "abertura"),
        (2, "FAZENDINHA",  "passagem",     "subida",  "passagem"),
        (3, "JURUTI",      "operacional",  "subida",  "carregamento"),
        (4, "FAZENDINHA",  "passagem",     "descida", "passagem"),
        (5, "BARRA_NORTE", "passagem",     "descida", "espera_mare"),
        (6, "ALUMAR",      "encerramento", "descida", "descarga"),
    ]


def test_marcos_exigidos_por_tipo_de_escala(conn):
    def marcos(tipo):
        return {r[0] for r in conn.execute(
            "SELECT tipo_evento FROM marco_exigido WHERE tipo_escala = ?", (tipo,))}

    assert marcos("operacional") == {"arrival", "berth", "unberth", "sailing"}
    # quem nao atraca nao tem berth nem unberth para dar
    assert marcos("passagem") == {"arrival", "sailing"}
    assert marcos("fundeio") == {"arrival", "sailing"}
    assert marcos("abertura") == {"sailing"}
    # quem fecha a viagem nao tem sailing: o proximo abre a viagem seguinte
    assert marcos("encerramento") == {"arrival", "berth", "unberth"}


def test_views_existem_e_aguentam_banco_vazio(conn):
    for view in ("evento_vigente", "escala_marcos", "escala_completa",
                 "escalas_incompletas", "escalas_a_conferir", "viagem_completa"):
        assert conn.execute("SELECT * FROM " + view).fetchall() == []


def test_inicializar_e_idempotente(conn):
    from app import db

    db.inicializar(conn)
    db.inicializar(conn)
    assert conn.execute("SELECT COUNT(*) FROM navio").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM rota_etapa").fetchone()[0] == 6


def test_os_testes_nunca_tocam_um_banco_de_verdade():
    """A fixture `cliente` apaga o arquivo apontado por SHIPOPS_BANCO.

    Herdar essa variavel do ambiente ja custou o banco de desenvolvimento uma
    vez. Este teste existe para que nunca custe o de producao.
    """
    import os
    import tempfile

    from app import config

    assert tempfile.gettempdir().lower() in config.CAMINHO_BANCO.lower()
    assert "shipops-testes-" in config.CAMINHO_BANCO
    assert not os.environ.get("TURSO_DATABASE_URL")
