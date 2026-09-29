# -*- coding: utf-8 -*-
"""Análises: a tabela dinâmica do Corsair.

Duas BASES planas, uma linha por viagem ou uma por pernada de viagem, e um
pivot em cima: dimensões nas linhas e nas colunas, uma medida agregada nas
células, filtros por dimensão. É a gramática do Power BI, sem sair do sistema.

Tudo é calculado em Python sobre poucas consultas — o Turso cobra por ida e
volta, e uma tabela dinâmica que fizesse uma consulta por célula não abriria.

Duas regras que decidem o número (Vinícius, 14/09/2026):
  1. O mês de uma viagem é o do ENCERRAMENTO (Unberth de Alumar): é quando a
     carga foi entregue. A viagem que sai em 28/12 e fecha em 12/01 é de janeiro.
  2. Viagem em curso não tem mês: aparece como "em curso" e só entra se o
     filtro de situação a deixar entrar.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from . import pernadas

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")

# chave -> (rótulo, bases em que existe)
DIMENSOES = {
    "navio":     ("Navio", ("viagens", "pernadas", "marcos")),
    "ano":       ("Ano", ("viagens", "pernadas", "marcos")),
    "trimestre": ("Trimestre", ("viagens", "pernadas", "marcos")),
    "mes":       ("Mês", ("viagens", "pernadas", "marcos")),
    "situacao":  ("Situação", ("viagens", "pernadas", "marcos")),
    "bunker":    ("Teve bunker", ("viagens", "pernadas")),
    "adicional": ("Parada adicional", ("viagens", "pernadas")),
    "grupo":     ("Grupo", ("pernadas",)),
    "pernada":   ("Pernada", ("pernadas",)),
    "viagem":    ("Viagem", ("pernadas", "marcos")),
    "porto":     ("Porto", ("marcos",)),
    "marco":     ("Marco", ("marcos",)),
    "lixo":      ("Lixo", ("marcos",)),
}

# chave -> (rótulo, unidade, casas decimais, bases)
MEDIDAS = {
    "viagens":          ("Viagens", "", 0, ("viagens", "pernadas")),
    "carregado":        ("Carregado", "MT", 0, ("viagens",)),
    "descarregado":     ("Descarregado", "MT", 0, ("viagens",)),
    "duracao":          ("Duração", "h", 1, ("viagens",)),
    "espera_berco":     ("Espera de berço", "h", 1, ("viagens",)),
    "atracado":         ("Tempo atracado", "h", 1, ("viagens",)),
    "vlsfo_consumido":  ("VLSFO consumido", "t", 3, ("viagens",)),
    "mgo_consumido":    ("MGO consumido", "t", 3, ("viagens",)),
    "vlsfo_abastecido": ("VLSFO abastecido", "t", 3, ("viagens",)),
    "mgo_abastecido":   ("MGO abastecido", "t", 3, ("viagens",)),
    "horas":            ("Horas", "h", 1, ("pernadas",)),
    "desvio":           ("Desvio da premissa", "h", 1, ("pernadas",)),
    # base Marcos: o que o comandante lanca em cada marco (set/2026: FW e lixo)
    "marcos":           ("Marcos", "", 0, ("marcos",)),
    "rob_vlsfo":        ("ROB VLSFO", "t", 3, ("marcos",)),
    "rob_mgo":          ("ROB MGO", "t", 3, ("marcos",)),
    "fw":               ("Água doce (FW)", "t", 1, ("marcos",)),
}

AGREGACOES = {
    "soma": "soma", "media": "média", "contagem": "contagem",
    "minimo": "mínimo", "maximo": "máximo",
}
BASES = {"viagens": "Viagens", "pernadas": "Pernadas", "marcos": "Marcos"}

EM_CURSO = ("9999-99", "em curso")   # ordena por último


def dimensoes_da(base: str) -> list[tuple[str, str]]:
    return [(k, v[0]) for k, v in DIMENSOES.items() if base in v[1]]


def medidas_da(base: str) -> list[tuple[str, str]]:
    return [(k, v[0]) for k, v in MEDIDAS.items() if base in v[3]]


# ---------------------------------------------------------------------------
# As bases
# ---------------------------------------------------------------------------

def _tempo(hora_local: str | None) -> dict:
    """ano, trimestre e mês da data de encerramento — cada um (ordem, rótulo)."""
    if not hora_local:
        return {"ano": EM_CURSO, "trimestre": EM_CURSO, "mes": EM_CURSO}
    d = datetime.fromisoformat(hora_local[:16])
    tri = (d.month - 1) // 3 + 1
    return {
        "ano": (str(d.year), str(d.year)),
        "trimestre": ("{}-{}".format(d.year, tri), "{}º tri {}".format(tri, d.year)),
        "mes": ("{}-{:02d}".format(d.year, d.month), "{}/{}".format(MESES[d.month - 1], d.year)),
    }


def _sim_nao(valor: bool) -> tuple:
    return (0, "sim") if valor else (1, "não")


def _horas(minutos) -> float | None:
    return None if minutos is None else round(minutos / 60, 2)


def _viagens(conn) -> list[dict]:
    """Uma linha por viagem, com as dimensões e todas as medidas já somadas."""
    viagens = conn.execute(
        "SELECT vg.id, vg.numero, vg.navio_id, vg.status, n.nome_oficial, "
        "       en.hora_local AS termino "
        "  FROM viagem vg JOIN navio n ON n.id = vg.navio_id "
        "  LEFT JOIN evento en ON en.id = vg.evento_encerramento_id "
        " WHERE vg.status IN ('aberta', 'encerrada') ORDER BY vg.navio_id, vg.id").fetchall()
    calculos = pernadas.calcular_em_lote(conn)

    carga = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT e.viagem_id, SUM(mc.carregado), SUM(mc.descarregado) "
        "  FROM movimento_carga mc JOIN escala e ON e.id = mc.escala_id "
        " WHERE e.status <> 'cancelada' GROUP BY e.viagem_id")}
    extras = defaultdict(set)
    for r in conn.execute(
            "SELECT viagem_id, motivo FROM escala "
            " WHERE origem = 'extra' AND status <> 'cancelada'"):
        extras[r[0]].add(r[1])
    abastecido = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT e.viagem_id, SUM(a.vlsfo), SUM(a.mgo) "
        "  FROM abastecimento a JOIN escala e ON e.id = a.escala_id "
        " WHERE e.status <> 'cancelada' GROUP BY e.viagem_id")}
    consumo = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
        "SELECT navio_id, viagem, SUM(consumo_vlsfo), SUM(consumo_mgo) "
        "  FROM consumo_combustivel WHERE consumo_vlsfo IS NOT NULL "
        " GROUP BY navio_id, viagem")}

    linhas = []
    for v in viagens:
        calc = calculos.get(v["id"]) or {}
        c = carga.get(v["id"], (None, None))
        a = abastecido.get(v["id"], (None, None))
        k = consumo.get((v["navio_id"], v["numero"]), (None, None))
        esperas = [calc.get("jur_espera_berco"), calc.get("alu_espera_berco")]
        atracados = [calc.get("jur_carregamento"), calc.get("alu_descarga")]
        linhas.append({
            "viagem_id": v["id"],
            "dims": {
                "navio": (v["navio_id"], v["nome_oficial"].replace("AMAZON ", "Amazon ").title()
                          .replace("Amazon ", "Amazon ")),
                **_tempo(v["termino"]),
                "situacao": (0, "encerrada") if v["status"] == "encerrada" else (1, "em curso"),
                "bunker": _sim_nao("bunker" in extras.get(v["id"], ())),
                "adicional": _sim_nao(bool(extras.get(v["id"]))),
                "viagem": (v["id"], v["numero"]),
            },
            "medidas": {
                "viagens": 1,
                "carregado": c[0], "descarregado": c[1],
                "duracao": _horas(calc.get("_duracao")),
                "espera_berco": _horas(sum(x for x in esperas if x is not None))
                if any(x is not None for x in esperas) else None,
                "atracado": _horas(sum(x for x in atracados if x is not None))
                if any(x is not None for x in atracados) else None,
                "vlsfo_consumido": k[0], "mgo_consumido": k[1],
                "vlsfo_abastecido": a[0], "mgo_abastecido": a[1],
            },
            "_calc": calc,
        })
    return linhas


def _pernadas(conn, viagens: list[dict]) -> list[dict]:
    """Uma linha por pernada de cada viagem. Horas só onde a pernada existiu inteira."""
    orcado = pernadas.premissas(conn)
    linhas = []
    for v in viagens:
        calc = v["_calc"]
        for i, (chave, grupo, nome, _sub, _de, _para) in enumerate(pernadas.PERNADAS):
            minutos = calc.get(chave)
            horas = _horas(minutos)
            linhas.append({
                "viagem_id": v["viagem_id"],
                "dims": {
                    **{k: val for k, val in v["dims"].items()},
                    "grupo": (pernadas.GRUPOS.index(grupo), grupo),
                    "pernada": (i, nome),
                },
                "medidas": {
                    "horas": horas,
                    "viagens": 1 if horas is not None else None,
                    "desvio": (round(horas - orcado[chave], 2)
                               if horas is not None and chave in orcado else None),
                },
            })
    return linhas


def _marcos(conn) -> list[dict]:
    """Uma linha por marco vigente: o que foi lancado, onde e quando.

    O mes aqui e o do PROPRIO marco (hora local), nao o do encerramento da
    viagem: a pergunta desta base e "o que aconteceu em tal mes", nao "quanto
    a viagem entregou".
    """
    ordem_marco = {"arrival": 0, "berth": 1, "unberth": 2, "sailing": 3}
    rotulo_marco = {"arrival": "Arrival", "berth": "Berth", "unberth": "Unberth", "sailing": "Sailing"}
    extras = defaultdict(set)
    for r in conn.execute(
            "SELECT viagem_id, motivo FROM escala WHERE origem = 'extra' AND status <> 'cancelada'"):
        extras[r[0]].add(r[1])
    portos = {r[0]: r[1] for r in conn.execute("SELECT codigo, nome FROM porto")}
    ordem_porto = {codigo: i for i, codigo in enumerate(sorted(portos))}
    linhas = []
    for r in conn.execute(
            "SELECT ev.tipo, ev.hora_local, ev.rob_vlsfo, ev.rob_mgo, ev.fw, ev.lixo, "
            "       e.codigo_porto, vg.id AS viagem_id, vg.numero, vg.status, vg.navio_id, "
            "       n.nome_oficial "
            "  FROM evento_vigente ev JOIN escala e ON e.id = ev.escala_id "
            "  JOIN viagem vg ON vg.id = e.viagem_id JOIN navio n ON n.id = vg.navio_id "
            " WHERE e.status <> 'cancelada' AND ev.hora_local IS NOT NULL "
            " ORDER BY vg.navio_id, ev.hora_utc"):
        lixo = (r["lixo"] or "").strip()
        linhas.append({
            "viagem_id": r["viagem_id"],
            "dims": {
                "navio": (r["navio_id"], r["nome_oficial"].replace("AMAZON ", "Amazon ").title()),
                **_tempo(r["hora_local"]),
                "situacao": (0, "encerrada") if r["status"] == "encerrada" else (1, "em curso"),
                "viagem": (r["viagem_id"], r["numero"]),
                "porto": (ordem_porto.get(r["codigo_porto"], 99), portos.get(r["codigo_porto"], r["codigo_porto"])),
                "marco": (ordem_marco.get(r["tipo"], 9), rotulo_marco.get(r["tipo"], r["tipo"])),
                "lixo": (0, lixo) if lixo else (1, "sem retirada"),
            },
            "medidas": {
                "marcos": 1,
                "rob_vlsfo": r["rob_vlsfo"], "rob_mgo": r["rob_mgo"], "fw": r["fw"],
            },
        })
    return linhas


def carregar(conn, base: str) -> list[dict]:
    if base == "marcos":
        return _marcos(conn)
    viagens = _viagens(conn)
    if base == "pernadas":
        return _pernadas(conn, viagens)
    for v in viagens:
        v["dims"].pop("viagem", None)
    return viagens


# ---------------------------------------------------------------------------
# O pivot
# ---------------------------------------------------------------------------

def agregar(valores: list, como: str):
    presentes = [v for v in valores if v is not None]
    if como == "contagem":
        return len(presentes)
    if not presentes:
        return None
    if como == "soma":
        return sum(presentes)
    if como == "media":
        return sum(presentes) / len(presentes)
    if como == "minimo":
        return min(presentes)
    if como == "maximo":
        return max(presentes)
    raise ValueError(como)


def valores_disponiveis(linhas: list[dict], base: str) -> dict[str, list[str]]:
    """dimensão -> rótulos distintos, na ordem natural. Alimenta os filtros."""
    saida = {}
    for chave, _rotulo in dimensoes_da(base):
        vistos = {l["dims"][chave] for l in linhas if chave in l["dims"]}
        saida[chave] = [v[1] for v in sorted(vistos)]
    return saida


def pivotar(linhas: list[dict], dims_linhas: list[str], dims_colunas: list[str],
            medida: str, como: str, filtros: dict[str, set]) -> dict:
    """A tabela: cabeçalhos, células, totais. Tudo já agregado."""
    def passa(l):
        return all(l["dims"].get(d, (None, ""))[1] in valores
                   for d, valores in filtros.items() if valores)

    dados = [l for l in linhas if passa(l)]
    baldes: dict[tuple, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
    chaves_l, chaves_c = set(), set()
    for l in dados:
        kl = tuple(l["dims"][d] for d in dims_linhas)
        kc = tuple(l["dims"][d] for d in dims_colunas)
        chaves_l.add(kl)
        chaves_c.add(kc)
        baldes[kl][kc].append(l["medidas"].get(medida))
    ordem_l, ordem_c = sorted(chaves_l), sorted(chaves_c)

    def total(kls, kcs):
        return agregar([v for kl in kls for kc in kcs for v in baldes[kl].get(kc, [])], como)

    tabela = []
    for kl in ordem_l:
        tabela.append({
            "rotulos": [r for _o, r in kl],
            "celulas": [agregar(baldes[kl].get(kc, []), como) for kc in ordem_c],
            "total": total([kl], ordem_c),
        })
    return {
        "linhas": tabela,
        "colunas": [[r for _o, r in kc] for kc in ordem_c],
        "totais_colunas": [total(ordem_l, [kc]) for kc in ordem_c],
        "total": total(ordem_l, ordem_c),
        "registros": len(dados),
        "de": len(linhas),
    }


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------

def formatar(valor, medida: str, como: str) -> str:
    if valor is None:
        return "—"
    casas = 0 if como == "contagem" else MEDIDAS[medida][2]
    if como == "media" and casas == 0:
        casas = 1
    texto = "{:,.{c}f}".format(valor, c=casas)
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def titulo(medida: str, como: str, dims_l: list[str], dims_c: list[str]) -> tuple[str, str]:
    rotulo, unidade, _c, _b = MEDIDAS[medida]
    nome = "{}{}, {}".format(rotulo, " ({})".format(unidade) if unidade else "", AGREGACOES[como])
    eixos = " × ".join(p for p in (
        " e ".join(DIMENSOES[d][0].lower() for d in dims_l) or "",
        " e ".join(DIMENSOES[d][0].lower() for d in dims_c) or "") if p)
    return nome, eixos


def csv(resultado: dict, dims_l: list[str], medida: str, como: str) -> str:
    """CSV que o Excel em português abre direto: BOM, ponto e vírgula, vírgula decimal."""
    def cel(v):
        return "" if v is None else formatar(v, medida, como).replace(".", "")
    cab = [DIMENSOES[d][0] for d in dims_l] + [" / ".join(c) or MEDIDAS[medida][0]
                                                for c in resultado["colunas"]] + ["Total"]
    saida = [";".join(cab)]
    for l in resultado["linhas"]:
        saida.append(";".join(l["rotulos"] + [cel(v) for v in l["celulas"]] + [cel(l["total"])]))
    saida.append(";".join(["Total"] + [""] * (len(dims_l) - 1)
                          + [cel(v) for v in resultado["totais_colunas"]] + [cel(resultado["total"])]))
    return "﻿" + "\r\n".join(saida) + "\r\n"


# ---------------------------------------------------------------------------
# O gráfico: as barras do pivot, em SVG desenhado no servidor
#
# Sem biblioteca — a CSP nao deixa carregar nada de fora, e um grafico de
# barras e geometria simples. As cores sao a paleta categorica validada
# (dataviz, 8 posicoes em ordem fixa); com uma serie so, a primeira. Acima de
# 8 series ou de 30 linhas o grafico nao se desenha: a tabela ja esta ali.
# ---------------------------------------------------------------------------

PALETA = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
LIMITE_SERIES, LIMITE_LINHAS, LIMITE_PONTOS = 8, 30, 120
LARGURA, ALTURA = 960, 360
MARGEM = {"esq": 64, "dir": 16, "topo": 18, "base": 54}
DIMENSOES_DE_TEMPO = ("ano", "trimestre", "mes")


def eh_serie_historica(dims_linhas: list[str]) -> bool:
    """A serie historica so faz sentido com o tempo nas linhas (e so ele)."""
    return len(dims_linhas) == 1 and dims_linhas[0] in DIMENSOES_DE_TEMPO


def _rotulo_curto_de_tempo(rotulo: str) -> str:
    """'jan/2026' -> 'jan/26'; '1º tri 2026' -> '1T26'; ano fica como esta."""
    if "/" in rotulo and len(rotulo) == 8:
        return rotulo[:4] + rotulo[6:]
    if "tri" in rotulo:
        return rotulo[0] + "T" + rotulo[-2:]
    return rotulo


def grafico_linha(resultado: dict, medida: str, como: str) -> dict | None:
    """A serie historica: o tempo no eixo x, uma linha por coluna do pivot.

    Linhas de 2px, marcadores de 8px com anel na cor do fundo, so o ultimo
    ponto de cada serie rotulado; o eixo x mostra um rotulo a cada k pontos
    para nunca se atropelar. Um ponto sem valor quebra a linha — nao se
    inventa zero onde nao houve viagem.
    """
    pontos = [l for l in resultado["linhas"] if l["rotulos"] and l["rotulos"][0] != EM_CURSO[1]]
    series = resultado["colunas"] or [[MEDIDAS[medida][0]]]
    n_p, n_s = len(pontos), len(series)
    if n_p < 2 or n_s > LIMITE_SERIES or n_p > LIMITE_PONTOS:
        return None
    valores = [v for l in pontos for v in l["celulas"] if v is not None]
    if not valores or min(valores) < 0:
        return None
    teto, passo = _escala_bonita(max(valores))
    plot_x, plot_y = MARGEM["esq"], MARGEM["topo"]
    plot_w = LARGURA - MARGEM["esq"] - MARGEM["dir"]
    plot_h = ALTURA - MARGEM["topo"] - MARGEM["base"]
    passo_x = plot_w / max(n_p - 1, 1)

    def x_de(i):
        return round(plot_x + i * passo_x, 1)

    def y_de(v):
        return round(plot_y + plot_h - (v / teto) * plot_h, 1)

    linhas_svg = []
    for j, s in enumerate(series):
        segmentos, atual, ultimo = [], [], None
        for i, l in enumerate(pontos):
            v = l["celulas"][j]
            if v is None:
                if atual:
                    segmentos.append(atual)
                atual = []
                continue
            atual.append({"x": x_de(i), "y": y_de(v), "valor": formatar(v, medida, como),
                          "titulo": "{} · {}: {}".format(l["rotulos"][0], " / ".join(s), formatar(v, medida, como))})
            ultimo = atual[-1]
        if atual:
            segmentos.append(atual)
        linhas_svg.append({
            "rotulo": " / ".join(s), "cor": PALETA[j],
            "caminhos": [" ".join("{}{},{}".format("M" if k == 0 else "L", p["x"], p["y"]) for k, p in enumerate(seg))
                         for seg in segmentos],
            "pontos": [p for seg in segmentos for p in seg],
            "ultimo": ultimo,
        })

    cada = max(1, -(-n_p // 12))                     # no maximo ~12 rotulos no eixo x
    eixo_x = [{"x": x_de(i), "rotulo": _rotulo_curto_de_tempo(l["rotulos"][0]), "titulo": l["rotulos"][0]}
              for i, l in enumerate(pontos) if i % cada == 0 or i == n_p - 1]
    ticks, v = [], 0.0
    while v <= teto + 1e-9:
        ticks.append({"y": y_de(v), "rotulo": formatar(v, medida, como) if v else "0"})
        v += passo
    return {
        "largura": LARGURA, "altura": ALTURA, "plot": {"x": plot_x, "y": plot_y, "w": plot_w, "h": plot_h},
        "series": linhas_svg, "eixo_x": eixo_x, "ticks": ticks,
        "legenda": [{"rotulo": s["rotulo"], "cor": s["cor"]} for s in linhas_svg] if n_s > 1 else [],
    }


def _escala_bonita(maximo: float, passos: int = 5) -> tuple[float, float]:
    """(teto, passo) com numeros redondos: 0 / 1.000 / 2.000, nao 0 / 1.234 / 2.468."""
    import math
    if maximo <= 0:
        return 1.0, 0.2
    bruto = maximo / passos
    potencia = 10 ** math.floor(math.log10(bruto))
    for m in (1, 2, 2.5, 5, 10):
        passo = m * potencia
        if passo >= bruto:
            break
    teto = math.ceil(maximo / passo) * passo
    return teto, passo


def grafico(resultado: dict, medida: str, como: str) -> dict | None:
    """Geometria das barras agrupadas: linhas do pivot no eixo x, uma serie por coluna."""
    linhas = resultado["linhas"]
    series = resultado["colunas"] or [[MEDIDAS[medida][0]]]
    # Mais series do que cores (meses nas colunas, por exemplo) e poucas
    # linhas: troca os eixos — as linhas viram series e as colunas, grupos.
    if len(series) > LIMITE_SERIES and 1 < len(linhas) <= LIMITE_SERIES:
        linhas = [{"rotulos": c, "celulas": [l["celulas"][j] for l in resultado["linhas"]]}
                  for j, c in enumerate(resultado["colunas"])]
        series = [l["rotulos"] for l in resultado["linhas"]]
    n_l, n_s = len(linhas), len(series)
    if not linhas or n_s > LIMITE_SERIES or n_l > LIMITE_LINHAS:
        return None
    valores = [v for l in linhas for v in l["celulas"] if v is not None]
    if not valores:
        return None
    maximo = max(valores)
    negativo = min(valores) < 0
    if negativo:
        return None                        # desvio negativo pede outro desenho; a tabela mostra
    teto, passo = _escala_bonita(maximo)

    plot_x, plot_y = MARGEM["esq"], MARGEM["topo"]
    plot_w = LARGURA - MARGEM["esq"] - MARGEM["dir"]
    plot_h = ALTURA - MARGEM["topo"] - MARGEM["base"]
    grupo_w = plot_w / n_l
    folga = max(8.0, grupo_w * 0.25)
    barra_w = min(24.0, (grupo_w - folga) / n_s - 2)
    if barra_w < 3:
        return None
    bloco_w = n_s * (barra_w + 2) - 2

    def y_de(v):
        return plot_y + plot_h - (v / teto) * plot_h

    # a barra mais alta de cada serie ganha o rotulo direto; as demais, so o title
    extremos = {}
    for j in range(n_s):
        col = [(l["celulas"][j], i) for i, l in enumerate(linhas) if l["celulas"][j] is not None]
        if col:
            extremos[j] = max(col)[1]

    grupos = []
    for i, l in enumerate(linhas):
        x0 = plot_x + i * grupo_w + (grupo_w - bloco_w) / 2
        barras = []
        for j, v in enumerate(l["celulas"]):
            x = x0 + j * (barra_w + 2)
            if v is None:
                barras.append({"x": round(x, 1), "y": None, "w": round(barra_w, 1), "h": 0,
                               "cor": PALETA[j], "titulo": "{} · {}: —".format(
                                   " / ".join(l["rotulos"]) or "Total", " / ".join(series[j]))})
                continue
            y = y_de(v)
            barras.append({
                "x": round(x, 1), "y": round(y, 1), "w": round(barra_w, 1),
                "h": round(plot_y + plot_h - y, 1), "cor": PALETA[j],
                "valor": formatar(v, medida, como),
                "rotular": extremos.get(j) == i,
                "titulo": "{} · {}: {}".format(" / ".join(l["rotulos"]) or "Total",
                                                " / ".join(series[j]), formatar(v, medida, como)),
            })
        rotulo = " / ".join(l["rotulos"]) or "Total"
        curto = " / ".join(_rotulo_curto_de_tempo(r) for r in l["rotulos"]) or "Total"
        grupos.append({"x": round(plot_x + i * grupo_w + grupo_w / 2, 1),
                       "rotulo": curto if len(curto) <= 16 else curto[:15] + "…",
                       "titulo": rotulo, "barras": barras})

    ticks = []
    v = 0.0
    while v <= teto + 1e-9:
        ticks.append({"y": round(y_de(v), 1), "rotulo": formatar(v, medida, "contagem" if como == "contagem" else como)
                      if v else "0"})
        v += passo
    return {
        "largura": LARGURA, "altura": ALTURA, "plot": {"x": plot_x, "y": plot_y, "w": plot_w, "h": plot_h},
        "grupos": grupos, "ticks": ticks,
        "legenda": [{"rotulo": " / ".join(s), "cor": PALETA[j]} for j, s in enumerate(series)] if n_s > 1 else [],
        # so inclina quando os rotulos nao cabem de pe: muitos grupos e texto longo
        "eixo_x_inclinado": n_l > 8 and any(len(g["rotulo"]) > 6 for g in grupos),
    }
