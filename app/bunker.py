# -*- coding: utf-8 -*-
"""Bunker: consumo de combustivel por pernada, e os abastecimentos.

A mesma gramatica das pernadas em horas, em toneladas. O consumo de uma pernada
e o ROB no marco de saida menos o ROB no marco de chegada, mais o que foi
abastecido no meio. O ROB de VLSFO e MGO ja e lancado em todo marco, entao
nada aqui pede dado novo ao comandante — e conta sobre o que existe.

Pernada em que falta o ROB num dos dois marcos vem como None: sem as duas
leituras nao ha consumo a afirmar. Diferente das horas, aqui a pernada em
curso tambem e None — o ROB de chegada ainda nao existe.
"""
from __future__ import annotations

from . import pernadas

COMBUSTIVEIS = ("vlsfo", "mgo")


def leituras(conn, navio_id: int) -> list:
    """Todas as leituras de ROB do navio, em ordem, com o abastecido de cada escala.

    `combustivel_bordo` so traz marcos com ROB de VLSFO lancado e prende o
    abastecimento da escala ao ULTIMO marco dela — e por isso que somar o
    abastecido entre dois instantes da o que entrou no meio da pernada.
    """
    return conn.execute(
        "SELECT hora_utc, rob_vlsfo, rob_mgo, abastecido_vlsfo, abastecido_mgo "
        "  FROM combustivel_bordo WHERE navio_id = ? ORDER BY hora_utc",
        (navio_id,)).fetchall()


def consumo_por_pernada(leituras_navio, calc: dict) -> dict[str, dict[str, float | None]]:
    """chave -> {'vlsfo': t, 'mgo': t} para cada pernada de `pernadas.calcular`."""
    por_hora = {l["hora_utc"]: l for l in leituras_navio}
    saida = {}
    for chave in pernadas.CHAVES:
        de, ate = calc["_de"].get(chave), calc["_ate"].get(chave)
        if chave == "sub_bunker_saida" and de is None:
            saida[chave] = {c: 0.0 for c in COMBUSTIVEIS}   # nao houve: zero
            continue
        if not de or not ate or de not in por_hora or ate not in por_hora:
            saida[chave] = {c: None for c in COMBUSTIVEIS}
            continue
        valores = {}
        for c in COMBUSTIVEIS:
            rob_de, rob_ate = por_hora[de]["rob_" + c], por_hora[ate]["rob_" + c]
            if rob_de is None or rob_ate is None:
                valores[c] = None
                continue
            abastecido = sum((l["abastecido_" + c] or 0.0) for l in leituras_navio
                             if de < l["hora_utc"] <= ate)
            valores[c] = round(rob_de + abastecido - rob_ate, 3)
        saida[chave] = valores
    return saida


def resumo_da_viagem(conn, viagem_id: int, leituras_navio, calc: dict) -> dict:
    """ROB na saida e no fim, abastecido, consumido e o consumo por dia da viagem."""
    por_hora = {l["hora_utc"]: l for l in leituras_navio}
    inicio, fim = calc["_inicio_utc"], calc["_termino_utc"]
    abastecido = conn.execute(
        "SELECT ROUND(SUM(a.vlsfo), 3), ROUND(SUM(a.mgo), 3) "
        "  FROM abastecimento a JOIN escala e ON e.id = a.escala_id "
        " WHERE e.viagem_id = ? AND e.status <> 'cancelada'", (viagem_id,)).fetchone()
    saida = {}
    for i, c in enumerate(COMBUSTIVEIS):
        rob_ini = por_hora[inicio]["rob_" + c] if inicio in por_hora else None
        rob_fim = por_hora[fim]["rob_" + c] if fim and fim in por_hora else None
        abast = abastecido[i] or 0.0
        consumido = (round(rob_ini + abast - rob_fim, 3)
                     if rob_ini is not None and rob_fim is not None else None)
        dias = calc["_duracao"] / 1440 if calc["_duracao"] else None
        saida[c] = {
            "rob_inicio": rob_ini, "rob_fim": rob_fim, "abastecido": abast,
            "consumido": consumido,
            "por_dia": round(consumido / dias, 2) if consumido is not None and dias else None,
        }
    return saida


def media(valores: list[float | None]) -> float | None:
    conhecidos = [v for v in valores if v is not None]
    return round(sum(conhecidos) / len(conhecidos), 3) if conhecidos else None


def degrau(valor: float | None, base: float | None) -> int:
    """0 a 3, so por razao: em toneladas nao ha o 'zero orcado' das horas."""
    if valor is None or not base or base <= 0:
        return 0
    razao = valor / base
    return 3 if razao > 1.6 else 2 if razao > 1.3 else 1 if razao > 1.15 else 0


def abastecimentos(conn, navio_id: int, limite: int = 20) -> list:
    """Os abastecimentos do navio, do mais recente ao mais antigo.

    O 'quando' e o ultimo marco lancado na parada — e o instante em que a
    quantidade passou a valer no ROB.
    """
    return conn.execute(
        "SELECT a.escala_id, a.vlsfo, a.mgo, a.nome_responsavel, "
        "       vg.numero, p.nome AS porto, e.origem, e.tipo_escala, "
        "       (SELECT MAX(v.hora_local) FROM evento_vigente v "
        "         WHERE v.escala_id = e.id) AS quando "
        "  FROM abastecimento a "
        "  JOIN escala e ON e.id = a.escala_id "
        "  JOIN viagem vg ON vg.id = e.viagem_id "
        "  JOIN porto p ON p.codigo = e.codigo_porto "
        " WHERE vg.navio_id = ? "
        " ORDER BY quando DESC, a.escala_id DESC LIMIT ?",
        (navio_id, limite)).fetchall()
