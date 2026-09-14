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
    "navio":     ("Navio", ("viagens", "pernadas")),
    "ano":       ("Ano", ("viagens", "pernadas")),
    "trimestre": ("Trimestre", ("viagens", "pernadas")),
    "mes":       ("Mês", ("viagens", "pernadas")),
    "situacao":  ("Situação", ("viagens", "pernadas")),
    "bunker":    ("Teve bunker", ("viagens", "pernadas")),
    "adicional": ("Parada adicional", ("viagens", "pernadas")),
    "grupo":     ("Grupo", ("pernadas",)),
    "pernada":   ("Pernada", ("pernadas",)),
    "viagem":    ("Viagem", ("pernadas",)),
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
}

AGREGACOES = {
    "soma": "soma", "media": "média", "contagem": "contagem",
    "minimo": "mínimo", "maximo": "máximo",
}
BASES = {"viagens": "Viagens", "pernadas": "Pernadas"}

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


def carregar(conn, base: str) -> list[dict]:
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
