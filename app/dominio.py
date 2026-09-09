"""Regras do dominio, em funcoes puras — sem banco, sem HTTP.

Molde herdado do account_service.py do Sistema Emissor: funcao pura, resultado
em tupla, erro devolvido como texto. Excecao aqui e falha de programacao, nao
lancamento invalido do comandante.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config

TIPOS_ESCALA = ("operacional", "fundeio", "passagem", "abertura")
TIPOS_EVENTO = ("arrival", "berth", "unberth", "sailing")
PRECISOES = ("exata", "periodo_am", "periodo_pm", "apenas_data", "tbc")
SENTIDOS = ("subida", "descida", "na")
ORIGENS = ("modelo", "extra", "abertura")
MOTIVOS = (
    "carregamento", "descarga", "espera_mare",
    "bunker", "docagem", "passagem", "abertura", "outro",
)

# A ordem cronologica em que os marcos tem de acontecer.
ORDEM_MARCO = {"arrival": 1, "berth": 2, "unberth": 3, "sailing": 4}

# Quais marcos cada tipo de escala aceita. Espelha a tabela marco_exigido —
# a tabela manda na validacao do banco, isto aqui atende o formulario.
MARCOS_POR_TIPO = {
    "operacional": ("arrival", "berth", "unberth", "sailing"),
    "fundeio": ("arrival", "sailing"),
    "passagem": ("arrival", "sailing"),
    "abertura": ("sailing",),
}

ROTULO_MARCO = {
    "arrival": "Arrival (chegada)",
    "berth": "Berth (atracacao)",
    "unberth": "Unberth (desatracacao)",
    "sailing": "Sailing (saida)",
}


def para_utc(hora_local: str, offset: str) -> str:
    """'2026-09-09T14:30' + '-03:00' -> '2026-09-09T17:30:00Z'.

    Guardar UTC e o que torna qualquer conta de duracao possivel sem gambiarra.
    Diferente do pipeline de e-mail, aqui o fuso e EXIGIDO: o comandante sabe,
    ele esta la.
    """
    momento = datetime.fromisoformat(f"{hora_local.strip()}{offset.strip()}")
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def erros_marco(
    tipo_escala: str,
    tipo_evento: str,
    hora_local: str | None,
    offset: str | None,
    nome_responsavel: str,
    precisao: str = "exata",
    marcos_existentes: dict[str, str] | None = None,
    agora_utc: str | None = None,
) -> list[str]:
    """Devolve TODOS os erros de uma vez, nao so o primeiro.

    `marcos_existentes` mapeia tipo -> hora_utc dos marcos ja vigentes na mesma
    escala, para checar a ordem cronologica.
    """
    erros: list[str] = []

    if tipo_evento not in TIPOS_EVENTO:
        return [f"Marco desconhecido: {tipo_evento!r}."]
    if tipo_escala not in MARCOS_POR_TIPO:
        return [f"Tipo de escala desconhecido: {tipo_escala!r}."]
    if precisao not in PRECISOES:
        erros.append(f"Precisao desconhecida: {precisao!r}.")

    # Cobrar berth de quem nao atracou e pedir dado que nao existe.
    if tipo_evento not in MARCOS_POR_TIPO[tipo_escala]:
        erros.append(
            f"Escala de {tipo_escala} nao tem {ROTULO_MARCO[tipo_evento]} — "
            f"o navio nao atraca aqui."
        )

    if not (nome_responsavel or "").strip():
        erros.append("Informe quem preencheu.")

    if precisao == "tbc":
        return erros  # sem horario, nao ha o que conferir adiante

    if not (hora_local or "").strip():
        erros.append("Informe a data e a hora.")
        return erros
    if not (offset or "").strip():
        erros.append("Informe o fuso horario.")
        return erros

    try:
        hora_utc = para_utc(hora_local, offset)
    except ValueError:
        erros.append(f"Data/hora ou fuso invalidos: {hora_local!r} {offset!r}.")
        return erros

    referencia = agora_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    limite = (
        datetime.fromisoformat(referencia.replace("Z", "+00:00"))
        + timedelta(hours=config.TOLERANCIA_FUTURO_HORAS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if hora_utc > limite:
        erros.append(
            f"{ROTULO_MARCO[tipo_evento]} esta no futuro "
            f"(mais de {config.TOLERANCIA_FUTURO_HORAS:g}h a frente)."
        )

    for outro, hora_outro in (marcos_existentes or {}).items():
        if outro == tipo_evento or outro not in ORDEM_MARCO or not hora_outro:
            continue
        if ORDEM_MARCO[outro] < ORDEM_MARCO[tipo_evento] and hora_outro > hora_utc:
            erros.append(
                f"{ROTULO_MARCO[tipo_evento]} nao pode ser anterior a "
                f"{ROTULO_MARCO[outro]}."
            )
        if ORDEM_MARCO[outro] > ORDEM_MARCO[tipo_evento] and hora_outro < hora_utc:
            erros.append(
                f"{ROTULO_MARCO[tipo_evento]} nao pode ser posterior a "
                f"{ROTULO_MARCO[outro]}."
            )

    return erros


def normalizar(texto: str) -> str:
    """MAIUSCULAS sem acento — a forma como alias de navio e de porto sao guardados."""
    import unicodedata

    sem_acento = unicodedata.normalize("NFD", texto or "")
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return sem_acento.strip().upper()
