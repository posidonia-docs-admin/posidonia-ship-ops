# -*- coding: utf-8 -*-
"""Análises: as bases planas e o pivot em cima delas."""
from app import analises, pernadas


def _linhas():
    def linha(navio, mes, situacao, descarregado, horas):
        return {"dims": {"navio": navio, "mes": mes, "situacao": situacao},
                "medidas": {"descarregado": descarregado, "viagens": 1, "horas": horas}}
    return [
        linha((1, "Pathfinder"), ("2026-01", "jan/2026"), (0, "encerrada"), 58000, 380.0),
        linha((1, "Pathfinder"), ("2026-02", "fev/2026"), (0, "encerrada"), 57500, 360.0),
        linha((2, "Pioneer"), ("2026-01", "jan/2026"), (0, "encerrada"), 56000, None),
        linha((2, "Pioneer"), analises.EM_CURSO, (1, "em curso"), None, None),
    ]


def test_pivot_soma_por_navio_e_mes():
    r = analises.pivotar(_linhas(), ["navio"], ["mes"], "descarregado", "soma",
                         {"situacao": {"encerrada"}})
    assert r["colunas"] == [["jan/2026"], ["fev/2026"]]
    assert [l["rotulos"] for l in r["linhas"]] == [["Pathfinder"], ["Pioneer"]]
    assert r["linhas"][0]["celulas"] == [58000, 57500] and r["linhas"][0]["total"] == 115500
    assert r["linhas"][1]["celulas"] == [56000, None]
    assert r["totais_colunas"] == [114000, 57500] and r["total"] == 171500
    assert (r["registros"], r["de"]) == (3, 4)


def test_media_ignora_o_que_nao_existe_e_contagem_conta_so_o_que_existe():
    r = analises.pivotar(_linhas(), ["navio"], [], "horas", "media", {})
    assert r["linhas"][0]["celulas"] == [370.0]          # Pathfinder: (380+360)/2
    assert r["linhas"][1]["celulas"] == [None]           # Pioneer: nenhuma inteira
    assert r["total"] == 370.0
    c = analises.pivotar(_linhas(), [], ["navio"], "horas", "contagem", {})
    assert c["totais_colunas"] == [2, 0] and c["total"] == 2
    assert c["linhas"][0]["rotulos"] == []               # sem linhas: uma so, "Total"


def test_a_ordem_e_a_natural_e_em_curso_vem_por_ultimo():
    r = analises.pivotar(_linhas(), ["mes"], [], "viagens", "soma", {})
    assert [l["rotulos"][0] for l in r["linhas"]] == ["jan/2026", "fev/2026", "em curso"]
    disponiveis = analises.valores_disponiveis(_linhas(), "viagens")
    assert disponiveis["navio"] == ["Pathfinder", "Pioneer"]
    assert disponiveis["situacao"] == ["encerrada", "em curso"]


def test_formato_em_portugues():
    assert analises.formatar(116848, "descarregado", "soma") == "116.848"
    assert analises.formatar(58.333, "horas", "media") == "58,3"
    assert analises.formatar(1421.5, "vlsfo_consumido", "soma") == "1.421,500"
    assert analises.formatar(None, "horas", "soma") == "—"
    assert analises.formatar(2, "viagens", "contagem") == "2"
    assert analises.formatar(1.5, "viagens", "media") == "1,5"


def test_csv_abre_no_excel_em_portugues():
    r = analises.pivotar(_linhas(), ["navio"], ["mes"], "descarregado", "soma", {})
    texto = analises.csv(r, ["navio"], "descarregado", "soma")
    assert texto.startswith("﻿Navio;jan/2026;fev/2026;em curso;Total\r\n")
    assert "Pathfinder;58000;57500;;115500" in texto
    assert texto.rstrip("\r\n").endswith("Total;114000;57500;;171500")


def test_a_base_de_viagens_sai_do_banco(conn):
    """Uma viagem inteira lançada: a base tem a linha dela, com carga, duração e mês."""
    from app import viagens as servico
    vid, _ = servico.abrir_viagem(conn, 1, por="teste")
    ids = {e["ordem"]: e["id"] for e in conn.execute(
        "SELECT ordem, id FROM escala WHERE viagem_id = ?", (vid,))}
    marcos = [(10, "sailing", "2026-03-01T18:40"), (20, "arrival", "2026-03-03T18:40"),
              (20, "sailing", "2026-03-03T19:40"), (30, "arrival", "2026-03-05T12:40"),
              (30, "berth", "2026-03-05T14:40"), (30, "unberth", "2026-03-06T16:40"),
              (30, "sailing", "2026-03-06T18:40"), (40, "arrival", "2026-03-08T06:40"),
              (40, "sailing", "2026-03-08T07:40"), (50, "arrival", "2026-03-08T14:40"),
              (50, "sailing", "2026-03-08T20:40"), (60, "arrival", "2026-03-10T08:40"),
              (60, "berth", "2026-03-12T08:40"), (60, "unberth", "2026-03-13T20:40")]
    servico.registrar_movimento_carga(conn, ids[30], quantidade=58000, nome_responsavel="x",
                                      registrado_por="t")
    servico.registrar_movimento_carga(conn, ids[60], quantidade=57500, nome_responsavel="x",
                                      registrado_por="t")
    for ordem, tipo, hora in marcos:
        _, erros = servico.lancar_marco(conn, ids[ordem], tipo=tipo, hora_local=hora,
                                        nome_responsavel="x", registrado_por="t")
        assert not erros, erros

    base = analises.carregar(conn, "viagens")
    fechada = next(l for l in base if l["dims"]["situacao"][1] == "encerrada")
    assert fechada["dims"]["navio"][1] == "Amazon Pathfinder"
    assert fechada["dims"]["mes"] == ("2026-03", "mar/2026")
    assert fechada["dims"]["trimestre"][1] == "1º tri 2026"
    assert fechada["dims"]["bunker"][1] == "não"
    m = fechada["medidas"]
    assert (m["carregado"], m["descarregado"]) == (58000, 57500)
    assert m["duracao"] == 290.0                          # 01/03 18:40 -> 13/03 20:40
    assert m["espera_berco"] == 2 + 48 and m["atracado"] == 26 + 36
    aberta = next(l for l in base if l["dims"]["situacao"][1] == "em curso")
    assert aberta["dims"]["mes"] == analises.EM_CURSO

    # a base de pernadas: 16 linhas por viagem, horas so onde existiu inteira
    per = analises.carregar(conn, "pernadas")
    assert len(per) == 2 * len(pernadas.PERNADAS)
    nav = next(l for l in per if l["viagem_id"] == vid and l["dims"]["pernada"][1].startswith("Navegação Alumar"))
    assert nav["medidas"]["horas"] == 48.0 and nav["dims"]["grupo"][1] == "Subida"
    # calcular_em_lote da o mesmo que calcular, viagem a viagem
    lote = pernadas.calcular_em_lote(conn)
    assert lote[vid] == pernadas.calcular(conn, vid)


def test_grafico_agrupa_barras_e_transpoe_quando_as_series_nao_cabem():
    r = analises.pivotar(_linhas(), ["navio"], ["mes"], "descarregado", "soma", {})
    g = analises.grafico(r, "descarregado", "soma")
    assert g and len(g["grupos"]) == 2 and len(g["grupos"][0]["barras"]) == 3
    assert len(g["legenda"]) == 3                       # tres series: legenda
    # so a barra mais alta de cada serie leva rotulo; "em curso" nao tem valor nenhum
    assert sum(1 for gr in g["grupos"] for b in gr["barras"] if b.get("rotular")) == 2
    assert g["ticks"][0]["rotulo"] == "0" and g["ticks"][-1]["rotulo"] == "60.000"

    # nove meses nas colunas e dois navios nas linhas: os eixos trocam de lugar
    muitas = [{"dims": {"navio": (1, "A"), "mes": ("2026-%02d" % m, "m%d" % m)}, "medidas": {"x": m}}
              for m in range(1, 10)] + [{"dims": {"navio": (2, "B"), "mes": ("2026-01", "m1")}, "medidas": {"x": 3}}]
    analises.MEDIDAS["x"] = ("X", "", 0, ("viagens",))
    try:
        r = analises.pivotar(muitas, ["navio"], ["mes"], "x", "soma", {})
        g = analises.grafico(r, "x", "soma")
        assert g and len(g["grupos"]) == 9 and len(g["legenda"]) == 2
        assert [s["rotulo"] for s in g["legenda"]] == ["A", "B"]
    finally:
        del analises.MEDIDAS["x"]

    # uma serie so: sem legenda; valor negativo: sem grafico
    r = analises.pivotar(_linhas(), ["navio"], [], "descarregado", "soma", {})
    assert analises.grafico(r, "descarregado", "soma")["legenda"] == []
    r = analises.pivotar([{"dims": {"navio": (1, "A")}, "medidas": {"horas": -2.0}}], ["navio"], [], "horas", "soma", {})
    assert analises.grafico(r, "horas", "soma") is None


def test_serie_historica_liga_os_meses_e_quebra_onde_nao_ha_valor():
    linhas = []
    for m, v in ((1, 100), (2, 120), (3, None), (4, 90), (5, 130)):
        linhas.append({"dims": {"navio": (1, "A"), "mes": ("2026-%02d" % m, "%s/2026" % analises.MESES[m - 1])},
                       "medidas": {"descarregado": v}})
    r = analises.pivotar(linhas, ["mes"], [], "descarregado", "soma", {})
    g = analises.grafico_linha(r, "descarregado", "soma")
    assert g and len(g["series"]) == 1 and g["legenda"] == []
    s = g["series"][0]
    assert len(s["caminhos"]) == 2 and len(s["pontos"]) == 4     # o buraco de marco quebra a linha
    assert s["ultimo"]["valor"] == "130"
    assert [e["rotulo"] for e in g["eixo_x"]] == ["jan/26", "fev/26", "mar/26", "abr/26", "mai/26"]
    assert analises.eh_serie_historica(["mes"]) and not analises.eh_serie_historica(["navio"])
    assert not analises.eh_serie_historica(["mes", "navio"])
    # com um ponto so nao ha serie
    r1 = analises.pivotar(linhas[:1], ["mes"], [], "descarregado", "soma", {})
    assert analises.grafico_linha(r1, "descarregado", "soma") is None
