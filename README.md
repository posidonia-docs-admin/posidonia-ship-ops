# Posidonia Ship Ops

Banco de dados operacional dos navios. Registra **Arrival, Berth, Unberth e Sailing**
por escala e por porto, lançados pelo comandante, supervisionados pela Posidonia e
consumidos pela equipe de analytics.

Escopo da Fase 1: os 4 navios **Amazon** (Ship Management — Alcoa), circuito de
bauxita **Alumar <-> Juruti**.

## Rodar

    python -m venv .venv
    .venv/Scripts/python -m pip install -r requirements-dev.txt
    .venv/Scripts/python -m pytest -q
    .venv/Scripts/python scripts/demo_viagem.py

## Variáveis de ambiente

Ver `.env.example`. `SHIPOPS_SECRET_KEY` é obrigatória — o app não sobe sem ela.
Sem `TURSO_DATABASE_URL`, usa SQLite local em `%LOCALAPPDATA%\PosidoniaShipOps`.

## Contexto e regras

`CLAUDE.md` — inclui as quatro regras que sustentam o modelo do circuito e as
pendências abertas com a operação.
