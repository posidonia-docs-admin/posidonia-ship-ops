"""Demonstracao ponta a ponta: duas viagens do Amazon Pathfinder.

Roda em banco de memoria, nao toca em nada. Serve para conferir de olho o que
a Fase 1 entrega.  Uso:  python scripts/demo_viagem.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, viagens  # noqa: E402

CMT = dict(nome_responsavel="Cmt. R. Almeida", registrado_por="navio.pathfinder")


def marcar(conn, escala_id, tipo, quando):
    _, erros = viagens.lancar_marco(conn, escala_id, tipo=tipo, hora_local=quando, **CMT)
    if erros:
        raise SystemExit("ERRO em {} {}: {}".format(tipo, quando, erros))


def escalas(conn, viagem_id):
    return conn.execute(
        "SELECT id, ordem, codigo_porto, tipo_escala FROM escala "
        " WHERE viagem_id = ? ORDER BY ordem", (viagem_id,)).fetchall()


conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
db.inicializar(conn)

# ---- viagem 1 -------------------------------------------------------------
v1, erros = viagens.abrir_viagem(conn, 1, por="vlo")
print("Viagem 1 aberta (id {}) — {} escalas criadas do modelo".format(
    v1, len(escalas(conn, v1))))

# uma parada de bunker nao prevista, depois da saida de Alumar
extra, _ = viagens.adicionar_escala_extra(
    conn, v1, codigo_porto="ICOARACI", tipo_escala="fundeio",
    motivo="bunker", apos_ordem=0, por="vlo",
    observacao="Entrada por Mosqueiro.")

por_ordem = {e["ordem"]: e["id"] for e in escalas(conn, v1)}

marcar(conn, por_ordem[0],  "sailing", "2026-08-01T18:00")   # abre a viagem
marcar(conn, extra,         "arrival", "2026-08-03T06:00")   # bunker
marcar(conn, extra,         "sailing", "2026-08-03T20:00")
marcar(conn, por_ordem[10], "arrival", "2026-08-04T09:00")   # Fazendinha subida
marcar(conn, por_ordem[10], "sailing", "2026-08-04T11:30")
marcar(conn, por_ordem[20], "arrival", "2026-08-06T04:00")   # Juruti
marcar(conn, por_ordem[20], "berth",   "2026-08-06T14:00")
marcar(conn, por_ordem[20], "unberth", "2026-08-08T09:00")
marcar(conn, por_ordem[20], "sailing", "2026-08-08T12:00")
marcar(conn, por_ordem[30], "arrival", "2026-08-10T07:00")   # Fazendinha descida
marcar(conn, por_ordem[30], "sailing", "2026-08-10T09:00")
marcar(conn, por_ordem[40], "arrival", "2026-08-11T02:00")   # Barra Norte, mare
marcar(conn, por_ordem[40], "sailing", "2026-08-11T09:30")
marcar(conn, por_ordem[50], "arrival", "2026-08-13T05:00")   # Alumar
marcar(conn, por_ordem[50], "berth",   "2026-08-14T08:00")
marcar(conn, por_ordem[50], "unberth", "2026-08-16T10:00")   # FECHA a viagem 1
marcar(conn, por_ordem[50], "sailing", "2026-08-16T14:00")   # ABRE a viagem 2

print("\n--- Escalas da viagem 1 " + "-" * 52)
cab = "{:>5} {:<12} {:<12} {:<8} {:>7} {:>7} {:>7} {:>7}"
print(cab.format("ordem", "porto", "tipo", "origem",
                 "espera", "atrac.", "pos-op", "total"))
for r in conn.execute(
        "SELECT ordem, codigo_porto, tipo_escala, origem, horas_espera_berco, "
        "       horas_atracado, horas_pos_operacao, horas_total_escala "
        "  FROM escala_completa WHERE viagem_id = ? ORDER BY ordem", (v1,)):
    def h(x):
        return "-" if x is None else "{:.1f}".format(x)
    print(cab.format(r["ordem"], r["codigo_porto"], r["tipo_escala"], r["origem"],
                     h(r["horas_espera_berco"]), h(r["horas_atracado"]),
                     h(r["horas_pos_operacao"]), h(r["horas_total_escala"])))

ok, avisos = viagens.encerrar_viagem(conn, v1)
print("\nEncerrada: {} {}".format(ok, avisos or ""))

# ---- viagem 2 -------------------------------------------------------------
v2, erros = viagens.abrir_viagem(conn, 1, por="vlo")
print("Viagem 2 aberta (id {}) — erros: {}".format(v2, erros))

ancora = conn.execute(
    "SELECT e.codigo_porto, ev.tipo, ev.hora_local "
    "  FROM viagem vg JOIN evento ev ON ev.id = vg.evento_abertura_id "
    "  JOIN escala e ON e.id = ev.escala_id WHERE vg.id = ?", (v2,)).fetchone()
print("Abre no {} {} de {} — o mesmo evento que fechou a escala da viagem 1".format(
    ancora["tipo"], ancora["hora_local"], ancora["codigo_porto"]))
print("Escalas da viagem 2: {} (sem escala de abertura, ela herdou o sailing)".format(
    len(escalas(conn, v2))))

print("\n--- Consolidado " + "-" * 60)
for r in conn.execute("SELECT * FROM viagem_completa ORDER BY viagem_id"):
    print("Viagem {} | {} | {} | {} escalas ({} extras) | {} marcos faltando | {} h".format(
        r["numero"], r["navio"], r["status"], r["escalas"], r["escalas_extras"],
        r["marcos_faltantes"], r["horas_viagem"]))
