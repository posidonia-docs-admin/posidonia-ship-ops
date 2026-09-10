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

def test_toda_viagem_nasce_com_as_seis_paradas(conn, pathfinder):
    """Sem caso especial de "primeira viagem": toda viagem comeca saindo de Alumar."""
    viagem_id, erros = viagens.abrir_viagem(conn, pathfinder, por="admin")
    assert erros == []

    linhas = [tuple(e) for e in escalas(conn, viagem_id)]
    assert linhas == [
        (10, "ALUMAR",      "abertura",     "na",      "abertura",     "modelo"),
        (20, "FAZENDINHA",  "passagem",     "subida",  "passagem",     "modelo"),
        (30, "JURUTI",      "operacional",  "subida",  "carregamento", "modelo"),
        (40, "FAZENDINHA",  "passagem",     "descida", "passagem",     "modelo"),
        (50, "BARRA_NORTE", "passagem",     "descida", "espera_mare",  "modelo"),
        (60, "ALUMAR",      "encerramento", "descida", "descarga",     "modelo"),
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
    assert any("já tem a viagem" in e for e in erros)


def test_codigo_da_viagem_segue_o_padrao_da_operacao(conn):
    """PREFIXO + ano com 2 digitos + sequencia de 3: APN26001."""
    import re
    from app.db import agora

    ano = agora()[2:4]
    for navio_id, prefixo in ((1, "APT"), (2, "APN"), (3, "ACM"), (4, "ACR")):
        viagem_id, erros = viagens.abrir_viagem(conn, navio_id)
        assert erros == []
        numero = conn.execute(
            "SELECT numero FROM viagem WHERE id = ?", (viagem_id,)).fetchone()[0]
        assert numero == "{}{}001".format(prefixo, ano), (navio_id, numero)
        assert re.fullmatch(r"[A-Z]{3}\d{2}\d{3}", numero)


def test_sequencia_avanca_e_e_por_navio(conn):
    from app.db import agora

    ano = agora()[2:4]

    def numero(vid):
        return conn.execute(
            "SELECT numero FROM viagem WHERE id = ?", (vid,)).fetchone()[0]

    v1, _ = viagens.abrir_viagem(conn, 2)          # Pioneer
    assert numero(v1) == "APN{}001".format(ano)

    outro, _ = viagens.abrir_viagem(conn, 3)       # Commander nao herda a contagem
    assert numero(outro) == "ACM{}001".format(ano)

    viagens.encerrar_viagem(conn, v1)              # sem unberth: nao fecha
    conn.execute("UPDATE viagem SET status = 'encerrada' WHERE id = ?", (v1,))
    conn.commit()
    v2, _ = viagens.abrir_viagem(conn, 2)
    assert numero(v2) == "APN{}002".format(ano)


def test_sequencia_usa_max_e_nao_contagem(conn):
    """Viagem cancelada nao pode fazer a proxima repetir um codigo ja usado."""
    from app.db import agora

    ano = agora()[2:4]
    v1, _ = viagens.abrir_viagem(conn, 2)
    conn.execute("UPDATE viagem SET status = 'cancelada' WHERE id = ?", (v1,))
    conn.commit()
    v2, _ = viagens.abrir_viagem(conn, 2)
    numero = conn.execute(
        "SELECT numero FROM viagem WHERE id = ?", (v2,)).fetchone()[0]
    assert numero == "APN{}002".format(ano)


def test_prefixos_cadastrados(conn):
    prefixos = {linha["nome_oficial"]: linha["prefixo"] for linha in conn.execute(
        "SELECT nome_oficial, prefixo FROM navio ORDER BY id")}
    assert prefixos == {
        "AMAZON PATHFINDER": "APT",
        "AMAZON PIONEER": "APN",
        "AMAZON COMMANDER": "ACM",
        "AMAZON COURAGE": "ACR",
    }


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
                  "operacional": ["arrival", "berth", "unberth", "sailing"],
                  "encerramento": ["arrival", "berth", "unberth"]}[tipo_escala]
        for marco in marcos:
            dia = base_dia + hora // 24
            _lancar(conn, escala_id, marco,
                    "2026-0{}-{:02d}T{:02d}:00".format(3, dia, hora % 24))
            hora += 3

    alumar = todas[-1][0]
    return viagem_id, alumar


def test_unberth_de_alumar_fecha_a_viagem_e_abre_a_seguinte_sozinho(conn, pathfinder):
    """O comandante nunca pensa em 'viagem' — so ve a proxima parada."""
    v1, _ = _viagem_completa(conn, pathfinder, base_dia=1)

    assert conn.execute(
        "SELECT status FROM viagem WHERE id = ?", (v1,)).fetchone()[0] == "encerrada"

    abertas = conn.execute(
        "SELECT id FROM viagem WHERE navio_id = ? AND status = 'aberta'",
        (pathfinder,)).fetchall()
    assert len(abertas) == 1 and abertas[0][0] != v1


def test_escala_que_fecha_a_viagem_nao_aceita_sailing(conn, pathfinder):
    """O sailing seguinte e o PRIMEIRO lancamento da proxima viagem, nunca um
    resto desta. Por isso a escala de encerramento nem oferece o campo."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    alumar = conn.execute(
        "SELECT id FROM escala WHERE viagem_id = ? AND ordem = 60", (viagem_id,)
    ).fetchone()[0]

    evento_id, erros = _lancar(conn, alumar, "sailing", "2026-03-15T10:00")
    assert evento_id is None
    assert erros

    faltantes = {r[0] for r in conn.execute(
        "SELECT marco_faltante FROM escalas_incompletas WHERE escala_id = ?", (alumar,))}
    assert faltantes == {"arrival", "berth", "unberth"}


def test_encadeamento_nao_dispara_sem_o_unberth(conn, pathfinder):
    """Sem unberth a viagem nao fecha, e a pendencia aparece na fila — nao some."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    alumar = conn.execute(
        "SELECT id FROM escala WHERE viagem_id = ? AND ordem = 60", (viagem_id,)
    ).fetchone()[0]

    _lancar(conn, alumar, "arrival", "2026-03-15T10:00")
    assert conn.execute(
        "SELECT status FROM viagem WHERE id = ?", (viagem_id,)).fetchone()[0] == "aberta"
    assert conn.execute(
        "SELECT COUNT(*) FROM escalas_incompletas WHERE escala_id = ? "
        "  AND marco_faltante = 'unberth'", (alumar,)).fetchone()[0] == 1


def test_a_viagem_seguinte_e_autocontida(conn, pathfinder):
    """Nada e herdado da anterior: ela nasce esperando o proprio Sailing."""
    v1, _ = _viagem_completa(conn, pathfinder, base_dia=1)
    v2 = conn.execute(
        "SELECT id FROM viagem WHERE navio_id = ? AND status = 'aberta'",
        (pathfinder,)).fetchone()[0]

    linhas = conn.execute(
        "SELECT ordem, codigo_porto, tipo_escala FROM escala "
        " WHERE viagem_id = ? ORDER BY ordem", (v2,)).fetchall()
    assert len(linhas) == 6
    assert linhas[0]["codigo_porto"] == "ALUMAR"
    assert linhas[0]["tipo_escala"] == "abertura"

    # ainda sem ancora: ela so existe quando o comandante lancar o Sailing
    assert conn.execute(
        "SELECT evento_abertura_id FROM viagem WHERE id = ?", (v2,)).fetchone()[0] is None

    # e o primeiro marco que falta e justamente esse Sailing
    primeiro = conn.execute(
        "SELECT marco_faltante FROM escalas_incompletas WHERE viagem_id = ? "
        " ORDER BY ordem, marco_ordem LIMIT 1", (v2,)).fetchone()[0]
    assert primeiro == "sailing"


def test_viagem_fecha_no_unberth_de_alumar(conn, pathfinder):
    v1, alumar1 = _viagem_completa(conn, pathfinder, base_dia=1)

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
        (10, "ALUMAR",      "modelo"),
        (15, "ICOARACI",    "extra"),     # encaixou entre 10 e 20
        (20, "FAZENDINHA",  "modelo"),
        (30, "JURUTI",      "modelo"),
        (40, "FAZENDINHA",  "modelo"),
        (50, "BARRA_NORTE", "modelo"),
        (60, "ALUMAR",      "modelo"),
    ]


def test_extras_sao_contaveis_para_medir_o_custo_em_tempo_das_paradas(conn, pathfinder):
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    viagens.adicionar_escala_extra(
        conn, viagem_id, codigo_porto="ITAQUI", tipo_escala="fundeio",
        motivo="bunker", apos_ordem=10)

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
    assert any("não cadastrado" in e for e in erros)


def test_sailing_da_saida_vira_a_ancora_de_inicio(conn, pathfinder):
    """Sem amarrar isso, a viagem fica sem hora de comeco e a duracao nunca sai."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    abertura_escala = conn.execute(
        "SELECT id FROM escala WHERE viagem_id = ? AND tipo_escala = 'abertura'",
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
    horas = conn.execute(
        "SELECT horas_viagem FROM viagem_completa WHERE viagem_id = ?", (v1,)
    ).fetchone()[0]
    assert horas is not None and horas > 0


def test_macapa_resolve_para_fazendinha(conn):
    """Mesma parada, duas grafias. Cadastrar dois portos duplicaria a escala."""
    for alias in ("MACAPA", "MACAPA/AP", "FAZENDINHA"):
        achado = conn.execute(
            "SELECT codigo_porto FROM porto_alias WHERE alias = ?", (alias,)).fetchone()
        assert achado is not None and achado[0] == "FAZENDINHA", alias
    assert conn.execute(
        "SELECT COUNT(*) FROM porto WHERE codigo = 'MACAPA'").fetchone()[0] == 0


def test_viagem_vazia_recebe_o_codigo_novo(conn, pathfinder):
    """A viagem aberta antes do padrao existir ainda pode ser renumerada."""
    from app.db import agora

    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    conn.execute("UPDATE viagem SET numero = '2026-001' WHERE id = ?", (viagem_id,))
    conn.commit()

    assert viagens.normalizar_viagens_vazias(conn) == 1
    numero = conn.execute(
        "SELECT numero FROM viagem WHERE id = ?", (viagem_id,)).fetchone()[0]
    assert numero == "APT{}001".format(agora()[2:4])


def test_viagem_com_marco_lancado_nao_e_renumerada(conn, pathfinder):
    """Depois do primeiro lancamento o codigo e definitivo."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    escala = conn.execute(
        "SELECT id FROM escala WHERE viagem_id = ? AND ordem = 30", (viagem_id,)
    ).fetchone()[0]
    _lancar(conn, escala, "arrival", "2026-03-01T08:00")

    conn.execute("UPDATE viagem SET numero = 'CODIGO-ANTIGO' WHERE id = ?", (viagem_id,))
    conn.commit()
    assert viagens.normalizar_viagens_vazias(conn) == 0
    assert conn.execute(
        "SELECT numero FROM viagem WHERE id = ?", (viagem_id,)
    ).fetchone()[0] == "CODIGO-ANTIGO"


def test_normalizar_e_idempotente(conn, pathfinder):
    viagens.abrir_viagem(conn, pathfinder)
    viagens.normalizar_viagens_vazias(conn)
    assert viagens.normalizar_viagens_vazias(conn) == 0


def test_viagem_vazia_com_rota_antiga_e_refeita(conn, pathfinder):
    """O caso de producao: a viagem aberta nasceu com a rota de 5 etapas."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    # simula a estrutura antiga: sem a saida de Alumar, com Alumar operacional no fim
    conn.execute("DELETE FROM escala WHERE viagem_id = ? AND ordem = 10", (viagem_id,))
    conn.execute("UPDATE escala SET tipo_escala = 'operacional' "
                 " WHERE viagem_id = ? AND ordem = 60", (viagem_id,))
    conn.commit()

    assert viagens.normalizar_viagens_vazias(conn) == 1

    linhas = conn.execute(
        "SELECT ordem, codigo_porto, tipo_escala FROM escala "
        " WHERE viagem_id = ? ORDER BY ordem", (viagem_id,)).fetchall()
    assert [tuple(l) for l in linhas] == [
        (10, "ALUMAR",      "abertura"),
        (20, "FAZENDINHA",  "passagem"),
        (30, "JURUTI",      "operacional"),
        (40, "FAZENDINHA",  "passagem"),
        (50, "BARRA_NORTE", "passagem"),
        (60, "ALUMAR",      "encerramento"),
    ]


def test_viagem_com_marco_nao_e_refeita(conn, pathfinder):
    """Depois do primeiro lancamento a estrutura e definitiva."""
    viagem_id, _ = viagens.abrir_viagem(conn, pathfinder)
    juruti = conn.execute(
        "SELECT id FROM escala WHERE viagem_id = ? AND ordem = 30", (viagem_id,)
    ).fetchone()[0]
    _lancar(conn, juruti, "arrival", "2026-03-01T08:00")

    conn.execute("DELETE FROM escala WHERE viagem_id = ? AND ordem = 10", (viagem_id,))
    conn.commit()
    assert viagens.normalizar_viagens_vazias(conn) == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM escala WHERE viagem_id = ?", (viagem_id,)
    ).fetchone()[0] == 5
