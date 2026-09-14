# -*- coding: utf-8 -*-
"""As pernadas da viagem: horas de um marco ao outro.

E a planilha "analise viagens" das supervisoras, calculada em vez de digitada.
Cada pernada e a diferenca entre dois marcos VIGENTES, em UTC — nunca em horario
local, que um dia vai diferir entre portos.

Ha dois graos de pernada, de proposito. As FINAS (espera de berco em Juruti,
espera de mare em Barra Norte) sao o que a supervisao acompanha. As de TOTAL
(Juruti -> Alumar, Operacao em Alumar) existem porque o orcamento de horas que
o Vinicius tem e mais grosso: "Navegacao Juruti x Alumar: 93,2h" atravessa
cinco pernadas finas. Sem a linha de total, a premissa dele nao teria onde morar.

A REFERENCIA de cada pernada e a premissa cadastrada em `premissa_pernada`
(horas orcadas, so admin); onde nao houver, a media das viagens encerradas do
proprio navio. A tela diz qual das duas esta usando.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .db import agora

# Seletores de escala. `origem: modelo` evita que uma parada adicional em
# Fazendinha (se um dia houver) se confunda com a do circuito.
ABERTURA = {"tipo_escala": "abertura"}
FAZ_SUBIDA = {"codigo_porto": "FAZENDINHA", "sentido": "subida", "origem": "modelo"}
JURUTI = {"codigo_porto": "JURUTI", "origem": "modelo"}
FAZ_DESCIDA = {"codigo_porto": "FAZENDINHA", "sentido": "descida", "origem": "modelo"}
BARRA_NORTE = {"codigo_porto": "BARRA_NORTE", "origem": "modelo"}
ENCERRAMENTO = {"tipo_escala": "encerramento"}

# (chave, grupo, nome, subtitulo, de, para)
# `de`/`para` sao (seletor, marco). A chave e o que a premissa referencia —
# mudar uma chave orfana a premissa cadastrada, entao elas nao mudam.
PERNADAS = (
    ("sub_nav_alumar_faz", "Subida", "Navegação Alumar → Fazendinha", "Sailing → Arrival",
     (ABERTURA, "sailing"), (FAZ_SUBIDA, "arrival")),
    ("sub_bunker_saida", "Subida", "Abastecimento na saída de Alumar",
     "parada adicional · Arrival → Sailing", None, None),
    ("sub_espera_faz", "Subida", "Espera em Fazendinha", "Arrival → Sailing · prático",
     (FAZ_SUBIDA, "arrival"), (FAZ_SUBIDA, "sailing")),
    ("sub_nav_faz_juruti", "Subida", "Navegação Fazendinha → Juruti", "Sailing → Arrival",
     (FAZ_SUBIDA, "sailing"), (JURUTI, "arrival")),

    ("jur_espera_berco", "Juruti", "Espera de berço", "Arrival → Berth",
     (JURUTI, "arrival"), (JURUTI, "berth")),
    ("jur_carregamento", "Juruti", "Carregamento", "Berth → Unberth",
     (JURUTI, "berth"), (JURUTI, "unberth")),
    ("jur_operacao", "Juruti", "Operação em Juruti (total)", "Arrival → Sailing",
     (JURUTI, "arrival"), (JURUTI, "sailing")),

    ("des_nav_juruti_faz", "Descida", "Navegação Juruti → Fazendinha", "Sailing → Arrival",
     (JURUTI, "sailing"), (FAZ_DESCIDA, "arrival")),
    ("des_espera_faz", "Descida", "Espera em Fazendinha", "Arrival → Sailing",
     (FAZ_DESCIDA, "arrival"), (FAZ_DESCIDA, "sailing")),
    ("des_nav_faz_bn", "Descida", "Navegação Fazendinha → Barra Norte", "Sailing → Arrival",
     (FAZ_DESCIDA, "sailing"), (BARRA_NORTE, "arrival")),
    ("des_espera_mare", "Descida", "Espera de maré em Barra Norte", "Arrival → Sailing",
     (BARRA_NORTE, "arrival"), (BARRA_NORTE, "sailing")),
    ("des_nav_bn_alumar", "Descida", "Navegação Barra Norte → Alumar", "Sailing → Arrival",
     (BARRA_NORTE, "sailing"), (ENCERRAMENTO, "arrival")),
    ("des_total", "Descida", "Juruti → Alumar (total)", "Sailing → Arrival",
     (JURUTI, "sailing"), (ENCERRAMENTO, "arrival")),

    ("alu_espera_berco", "Alumar", "Espera de berço", "Arrival → Berth",
     (ENCERRAMENTO, "arrival"), (ENCERRAMENTO, "berth")),
    ("alu_descarga", "Alumar", "Descarga", "Berth → Unberth",
     (ENCERRAMENTO, "berth"), (ENCERRAMENTO, "unberth")),
    ("alu_operacao", "Alumar", "Operação em Alumar (total)", "Arrival → Unberth",
     (ENCERRAMENTO, "arrival"), (ENCERRAMENTO, "unberth")),
)

CHAVES = tuple(p[0] for p in PERNADAS)
GRUPOS = ("Subida", "Juruti", "Descida", "Alumar")


# ---------------------------------------------------------------------------
# Calculo
# ---------------------------------------------------------------------------

def _minutos(de: str | None, para: str | None) -> int | None:
    try:
        delta = datetime.fromisoformat(para) - datetime.fromisoformat(de)
    except (TypeError, ValueError):
        return None
    minutos = int(delta.total_seconds() // 60)
    return minutos if minutos >= 0 else None


def _casa(escala, seletor: dict) -> bool:
    return all(escala[campo] == valor for campo, valor in seletor.items())


def _marcos_da_viagem(conn, viagem_id: int):
    """escala -> {tipo: hora_utc}, so marcos vigentes, so escalas nao canceladas."""
    escalas = conn.execute(
        "SELECT id, ordem, codigo_porto, tipo_escala, sentido, motivo, origem "
        "  FROM escala WHERE viagem_id = ? AND status <> 'cancelada' ORDER BY ordem",
        (viagem_id,)).fetchall()
    marcos = {}
    for escala in escalas:
        marcos[escala["id"]] = {
            r["tipo"]: r["hora_utc"] for r in conn.execute(
                "SELECT tipo, hora_utc FROM evento_vigente WHERE escala_id = ?",
                (escala["id"],))}
    return escalas, marcos


def _hora(escalas, marcos, ponto) -> str | None:
    seletor, marco = ponto
    for escala in escalas:
        if _casa(escala, seletor):
            return marcos[escala["id"]].get(marco)
    return None


def _bunker_na_saida(escalas, marcos) -> int:
    """Soma das paradas adicionais de bunker entre a saida de Alumar e Fazendinha.

    Zero quando nao houve: e uma pernada que nao acontece na maioria das
    viagens, e zero horas e a resposta certa, nao 'sem dado'.
    """
    limite = next((e["ordem"] for e in escalas if _casa(e, FAZ_SUBIDA)), None)
    total = 0
    for escala in escalas:
        if escala["origem"] != "extra" or escala["motivo"] != "bunker":
            continue
        if limite is not None and escala["ordem"] > limite:
            continue
        m = marcos[escala["id"]]
        minutos = _minutos(m.get("arrival"), m.get("sailing"))
        if minutos:
            total += minutos
    return total


def _janela_bunker(escalas, marcos):
    """(arrival, sailing) da PRIMEIRA parada adicional de bunker antes de Fazendinha."""
    limite = next((e["ordem"] for e in escalas if _casa(e, FAZ_SUBIDA)), None)
    for escala in escalas:
        if escala["origem"] == "extra" and escala["motivo"] == "bunker"                 and (limite is None or escala["ordem"] < limite):
            m = marcos[escala["id"]]
            return m.get("arrival"), m.get("sailing")
    return None, None


def calcular(conn, viagem_id: int) -> dict:
    """Minutos de cada pernada desta viagem, mais o resumo.

    Pernada sem os dois marcos vem como None: ainda nao aconteceu, ou faltou
    lancamento. Quem chama decide como mostrar.
    """
    return _calcular(*_marcos_da_viagem(conn, viagem_id))


def calcular_em_lote(conn) -> dict[int, dict]:
    """viagem_id -> o mesmo que `calcular`, para TODAS as viagens, em duas consultas.

    A tela de Analises precisa das pernadas de 150+ viagens de uma vez. Uma
    consulta por viagem sao centenas de idas ao Turso; aqui sao duas.
    """
    escalas = conn.execute(
        "SELECT id, viagem_id, ordem, codigo_porto, tipo_escala, sentido, motivo, origem "
        "  FROM escala WHERE status <> 'cancelada' ORDER BY viagem_id, ordem").fetchall()
    marcos = defaultdict(dict)
    for r in conn.execute(
            "SELECT ev.escala_id, ev.tipo, ev.hora_utc FROM evento_vigente ev "
            "  JOIN escala e ON e.id = ev.escala_id WHERE e.status <> 'cancelada'"):
        marcos[r["escala_id"]][r["tipo"]] = r["hora_utc"]
    por_viagem = defaultdict(list)
    for e in escalas:
        por_viagem[e["viagem_id"]].append(e)
    return {vid: _calcular(escs, {e["id"]: marcos.get(e["id"], {}) for e in escs})
            for vid, escs in por_viagem.items()}


def _calcular(escalas, marcos) -> dict:
    saida = {}
    # O instante em que cada pernada COMECOU. E o que deixa a tela dizer
    # "10h55 e correndo" numa pernada que ainda nao terminou.
    comecos, fins = {}, {}
    for chave, _grupo, _nome, _sub, de, para in PERNADAS:
        if chave == "sub_bunker_saida":
            saida[chave] = _bunker_na_saida(escalas, marcos)
            comecos[chave], fins[chave] = _janela_bunker(escalas, marcos)
        else:
            inicio_pernada = _hora(escalas, marcos, de)
            fim_pernada = _hora(escalas, marcos, para)
            saida[chave] = _minutos(inicio_pernada, fim_pernada)
            comecos[chave], fins[chave] = inicio_pernada, fim_pernada
    # Comeco e fim de cada pernada, em UTC. O Bunker le os dois: o consumo da
    # pernada e o ROB no comeco menos o ROB no fim, mais o abastecido no meio.
    saida["_de"] = comecos
    saida["_ate"] = fins

    inicio = _hora(escalas, marcos, (ABERTURA, "sailing"))
    termino = _hora(escalas, marcos, (ENCERRAMENTO, "unberth"))
    saida["_inicio_utc"] = inicio
    saida["_termino_utc"] = termino
    saida["_duracao"] = _minutos(inicio, termino)
    return saida


# ---------------------------------------------------------------------------
# Premissas e referencia
# ---------------------------------------------------------------------------

def premissas(conn) -> dict[str, float]:
    """chave -> horas orcadas. So o que foi cadastrado."""
    return {r["chave"]: r["horas"] for r in conn.execute(
        "SELECT chave, horas FROM premissa_pernada WHERE horas IS NOT NULL")}


def gravar_premissas(conn, valores: dict[str, float | None], por: str) -> list[str]:
    """Grava o orcamento de horas. Vazio apaga — volta a valer a media."""
    erros = []
    quando = agora()
    for chave, horas in valores.items():
        if chave not in CHAVES:
            erros.append("Pernada desconhecida: {}".format(chave))
            continue
        if horas is None:
            conn.execute("DELETE FROM premissa_pernada WHERE chave = ?", (chave,))
            continue
        if horas < 0:
            erros.append("Horas negativas em {}.".format(chave))
            continue
        conn.execute(
            "INSERT INTO premissa_pernada (chave, horas, atualizado_por, atualizado_em) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET horas = excluded.horas, "
            "  atualizado_por = excluded.atualizado_por, atualizado_em = excluded.atualizado_em",
            (chave, float(horas), por, quando))
    conn.commit()
    return erros


def referencia(chave: str, orcado: dict[str, float], historico: list[dict]) -> tuple[int | None, str]:
    """(minutos, 'orçado' | 'média' | '') para uma pernada.

    Orcado ganha da media. A media so conta viagens em que a pernada existiu
    inteira — uma pernada sem os dois marcos nao entra, nem como zero.
    """
    if chave in orcado:
        return int(round(orcado[chave] * 60)), "orçado"
    valores = [v[chave] for v in historico if v.get(chave) is not None]
    if not valores:
        return None, ""
    return int(round(sum(valores) / len(valores))), "média"


def degrau(minutos: int | None, base: int | None) -> int:
    """0 a 3: quanto a pernada passou da referencia.

    Sobre a media: 1,15x, 1,3x e 1,6x. Uma referencia ZERO (o Vinicius orca
    "espera em Macapa: 0h") nao admite razao — ali os degraus sao em horas
    absolutas: acima de 1h, de 3h e de 6h. Sao chutes calibraveis; o que nao
    pode e dividir por zero e sumir com o alerta justamente na espera que
    deveria ser zero.
    """
    if minutos is None or base is None:
        return 0
    if base == 0:
        return 3 if minutos > 360 else 2 if minutos > 180 else 1 if minutos > 60 else 0
    razao = minutos / base
    return 3 if razao > 1.6 else 2 if razao > 1.3 else 1 if razao > 1.15 else 0
