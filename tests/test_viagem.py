"""Abertura de viagem, o ciclo que abre e fecha em Alumar, e a escala extra."""

from app import viagens


def escalas(conn, viagem_id):
    return conn.execute(
        "SELECT ordem, codigo_porto, tipo_escala, sentido, motivo, origem "
        "  FROM escala WHERE viagem_id = ? ORDER BY ordem", (viagem_id,)
    ).fetchall()


def _lancar(conn, escala_id, tipo, hora, **kw):
    return viagens.lancar_marco(
        conn, escala_id, tipo=tipo, hora_local=hora,
        nome_responsavel="Cmt. Teste", registrado_por="navio.pathfinder", **kw)


# ---------------------------------------------------------------------------

def test_abrir_primeira_viagem_cria_escalas_do_modelo_mais_a_de_abertura(conn, pathfinder):
    viagem_id, erros = viagens.abrir_viagem(conn, pathfinder, por="admin")
    assert erros == []

    linhas = [tuple(e) for e in escalas(conn, viagem_id)]
    assert linhas == [
        (0,  "ALUMAR",      "abertura",    "na",      "abertura",     "abertura"),
        (10, "FAZENDINHA",  "passagem",    "subida",  "passagem",     "modelo"),
        (20, "JURUTI",      "operacional", "subida",  "carregamento", "modelo"),
        (30, "FAZENDINHA",  "passagem",    "descida", "passagem",     "modelo"),
        (40, "BARRA_NORTE", "passagem",    "descida", "espera_mare",  "modelo"),
        (50, "ALUMAR",      "operacional", "descida", "descarga",     "modelo"),
    ]


def test_fazendinha_aparece_duas_vezes_e_sentido_as_separa(conn, pathfinder):
    """E por isso que a chave da escala e (viagem, ordem) e nunca (viagem, porto)."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    fz = conn.execute(
        "SELECT ordem, sentido FROM escala "
        " WHERE viagem_id = ? AND codigo_porto = 'FAZENDINHA' ORDER BY ordem",
        (viagem_id,)).fetchall()
    assert len(fz) == 2
    assert [r[1] for r in fz] == ["subida", "descida"]


def test_um_navio_so_tem_uma_viagem_aberta(conn, pathfinder):
    viagens.abrir_viagem(conn, pathfinder)
    segunda, erros = viagens.abrir_viagem(conn, pathfinder)
    assert segunda is None
    assert any("ja tem a viagem" in e for e in erros)


def test_numero_da_viagem_e_sequencial_por_navio(conn, pathfinder):
    v1, _ = viagens.abrir_viagem(conn, pathfinder)
    numero = conn.execute("SELECT numero FROM viagem WHERE id = ?", (v1,)).fetchone()[0]
    assert numero.endswith("-001")


# ---------------------------------------------------------------------------
# O ciclo: o sailing de Alumar fecha uma viagem e abre a seguinte
# ---------------------------------------------------------------------------

def _viagem_completa(conn, pathfinder, base_dia):
    """Preenche uma viagem inteira. Devolve (viagem_id, id da escala de Alumar)."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    todas = conn.execute(
        "SELECT id, ordem, tipo_escala FROM escala WHERE viagem_id = ? ORDER BY ordem",
        (viagem_id,)).fetchall()

    hora = 0
    for escala_id, _ordem, tipo_escala in todas:
        marcos = {"abertura": ["sailing"],
                  "passagem": ["arrival", "sailing"],
                  "operacional": ["arrival", "berth", "unberth", "sailing"]}[tipo_escala]
        for marco in marcos:
            dia = base_dia + hora // 24
            _lancar(conn, escala_id, marco,
                    "2026-0{}-{:02d}T{:02d}:00".format(3, dia, hora % 24))
            hora += 3

    alumar = todas[-1][0]
    return viagem_id, alumar


def test_segunda_viagem_herda_o_sailing_de_alumar_e_nao_cria_escala_de_abertura(
        conn, pathfinder):
    v1, alumar1 = _viagem_completa(conn, pathfinder, base_dia=1)
    ok, _ = viagens.encerrar_viagem(conn, v1)
    assert ok

    v2, erros = viagens.abrir_viagem(conn, pathfinder)
    assert erros == []

    # a segunda viagem nao repete a escala de Alumar: ela a herda por evento-ancora
    origens = [r[0] for r in conn.execute(
        "SELECT origem FROM escala WHERE viagem_id = ? ORDER BY ordem", (v2,))]
    assert "abertura" not in origens
    assert len(origens) == 5

    abertura_id = conn.execute(
        "SELECT evento_abertura_id FROM viagem WHERE id = ?", (v2,)).fetchone()[0]
    assert abertura_id is not None

    # e o evento-ancora e exatamente o sailing da escala de Alumar da viagem 1
    dono = conn.execute(
        "SELECT escala_id, tipo FROM evento WHERE id = ?", (abertura_id,)).fetchone()
    assert dono[0] == alumar1
    assert dono[1] == "sailing"


def test_viagem_fecha_no_unberth_de_alumar_nao_no_sailing(conn, pathfinder):
    v1, alumar1 = _viagem_completa(conn, pathfinder, base_dia=1)
    viagens.encerrar_viagem(conn, v1)

    encerramento = conn.execute(
        "SELECT evento_encerramento_id FROM viagem WHERE id = ?", (v1,)).fetchone()[0]
    tipo = conn.execute(
        "SELECT tipo, escala_id FROM evento WHERE id = ?", (encerramento,)).fetchone()
    assert tipo[0] == "unberth"
    assert tipo[1] == alumar1


def test_nao_encerra_sem_o_unberth_de_alumar(conn, pathfinder):
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    ok, erros = viagens.encerrar_viagem(conn, viagem_id)
    assert ok is False
    assert any("Unberth de ALUMAR" in e for e in erros)


# ---------------------------------------------------------------------------
# Escala extra — o eventual
# ---------------------------------------------------------------------------

def test_escala_extra_de_bunker_entra_na_posicao_certa(conn, pathfinder):
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)

    escala_id, erros = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ICOARACI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10, por="admin")
    assert erros == []
    assert escala_id is not None

    ordem = [(r[0], r[1], r[5]) for r in escalas(conn, viagem_id)]
    assert ordem == [
        (0,  "ALUMAR",      "abertura"),
        (10, "FAZENDINHA",  "modelo"),
        (15, "ICOARACI",    "extra"),     # encaixou entre 10 e 20
        (20, "JURUTI",      "modelo"),
        (30, "FAZENDINHA",  "modelo"),
        (40, "BARRA_NORTE", "modelo"),
        (50, "ALUMAR",      "modelo"),
    ]


def test_extras_sao_contaveis_para_medir_o_custo_em_tempo_das_paradas(conn, pathfinder):
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ITAQUI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=40)

    extras = conn.execute(
        "SELECT escalas_extras FROM viagem_completa WHERE viagem_id = ?",
        (viagem_id,)).fetchone()[0]
    assert extras == 1


def test_escala_extra_recusa_porto_desconhecido(conn, pathfinder):
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    escala_id, erros = viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="SANTOS", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10)
    assert escala_id is None
    assert any("nao cadastrado" in e for e in erros)


def test_sailing_da_escala_de_abertura_vira_ancora_da_primeira_viagem(conn, pathfinder):
    """Sem amarrar isso, a primeira viagem de um navio fica para sempre sem hora
    de inicio — e a duracao da viagem nunca sai."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    abertura_escala = conn.execute(
        "SELECT id FROM escala WHERE viagem_id = ? AND origem = 'abertura'",
        (viagem_id,)).fetchone()[0]

    assert conn.execute(
        "SELECT evento_abertura_id FROM viagem WHERE id = ?", (viagem_id,)
    ).fetchone()[0] is None

    evento_id, erros = _lancar(conn, abertura_escala, "sailing", "2026-03-01T18:00")
    assert erros == []
    assert conn.execute(
        "SELECT evento_abertura_id FROM viagem WHERE id = ?", (viagem_id,)
    ).fetchone()[0] == evento_id


def test_duracao_da_viagem_sai_do_sailing_ao_unberth_de_alumar(conn, pathfinder):
    v1, _ = _viagem_completa(conn, pathfinder, base_dia=1)
    viagens.encerrar_viagem(conn, v1)
    horas = conn.execute(
        "SELECT horas_viagem FROM viagem_completa WHERE viagem_id = ?", (v1,)
    ).fetchone()[0]
    assert horas is not None and horas > 0
