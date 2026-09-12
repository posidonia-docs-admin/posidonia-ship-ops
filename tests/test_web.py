"""Camada web: guarda de login, isolamento entre navios e a API da fila local."""

import os
import pathlib
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import contas, db, main

SENHA = "senha-de-teste-123"


@pytest.fixture()
def cliente():
    """Banco em arquivo, limpo a cada teste, com as contas do piloto criadas."""
    caminho = pathlib.Path(os.environ["SHIPOPS_BANCO"])
    if caminho.exists():
        caminho.unlink()
    db.inicializar()
    with closing(db.conectar()) as conn:
        contas.criar_conta(conn, login="navio.pathfinder", senha=SENHA,
                           nome_exibicao="Amazon Pathfinder", perfil="navio", navio_id=1)
        contas.criar_conta(conn, login="navio.pioneer", senha=SENHA,
                           nome_exibicao="Amazon Pioneer", perfil="navio", navio_id=2)
        contas.criar_conta(conn, login="vlo", senha=SENHA,
                           nome_exibicao="Vinicius", perfil="supervisor")
        contas.criar_conta(conn, login="bi", senha=SENHA,
                           nome_exibicao="Analytics", perfil="analytics")
    with TestClient(main.app) as c:
        yield c


def entrar(cliente, login="navio.pathfinder"):
    resposta = cliente.post("/login", data={"login": login, "senha": SENHA, "next": "/"},
                            follow_redirects=False)
    assert resposta.status_code == 303
    return resposta


def escala_de(cliente, ordem=30):
    with closing(db.conectar()) as conn:
        return conn.execute(
            "SELECT e.id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE vg.status = 'aberta' AND e.ordem = ? ORDER BY vg.navio_id LIMIT 1",
            (ordem,)).fetchone()[0]


# ---------------------------------------------------------------------------
# Guarda de login
# ---------------------------------------------------------------------------

def test_health_e_publico_e_nao_anuncia_versao(cliente):
    resposta = cliente.get("/api/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}


def test_rota_protegida_manda_para_o_login_preservando_o_destino(cliente):
    resposta = cliente.get("/navio", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/login?next=")
    assert "%2Fnavio" in resposta.headers["location"]


def test_senha_errada_nao_entra(cliente):
    resposta = cliente.post("/login", data={"login": "navio.pathfinder", "senha": "x"},
                            follow_redirects=False)
    assert resposta.status_code == 303
    assert "erro=" in resposta.headers["location"]
    assert cliente.cookies.get("shipops_sessao") is None


def test_login_valido_entra_e_abre_a_viagem_sozinho(cliente):
    entrar(cliente)
    resposta = cliente.get("/navio")
    assert resposta.status_code == 200
    assert "AMAZON PATHFINDER" in resposta.text
    # a home abre a viagem se nao houver nenhuma: o comandante nunca fica sem onde lancar
    assert "Juruti" in resposta.text and "Barra Norte" in resposta.text


def test_cookie_adulterado_e_recusado(cliente):
    entrar(cliente)
    cliente.cookies.set("shipops_sessao", "bWVudGlyYQ.YXNzaW5hdHVyYQ")
    resposta = cliente.get("/navio", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/login")


def test_next_nao_permite_redirect_para_fora(cliente):
    resposta = cliente.post(
        "/login",
        data={"login": "navio.pathfinder", "senha": SENHA, "next": "//evil.example"},
        follow_redirects=False)
    assert resposta.headers["location"] == "/"


def test_cabecalhos_de_seguranca(cliente):
    resposta = cliente.get("/login")
    assert "default-src 'self'" in resposta.headers["content-security-policy"]
    assert resposta.headers["x-frame-options"] == "DENY"
    assert resposta.headers["x-content-type-options"] == "nosniff"
    assert resposta.headers["cache-control"] == "no-store"


def test_swagger_desligado(cliente):
    for rota in ("/docs", "/redoc", "/openapi.json"):
        assert cliente.get(rota, follow_redirects=False).status_code in (303, 404)


# ---------------------------------------------------------------------------
# Perfis
# ---------------------------------------------------------------------------

def test_analytics_nao_escreve(cliente):
    entrar(cliente, "bi")
    resposta = cliente.post("/api/marco", json={})
    assert resposta.status_code == 403
    assert "somente leitura" in resposta.text


def test_supervisor_cai_no_painel(cliente):
    entrar(cliente, "vlo")
    resposta = cliente.get("/", follow_redirects=False)
    assert resposta.headers["location"] == "/painel"
    assert "Frota" in cliente.get("/painel").text


# ---------------------------------------------------------------------------
# API da fila local
# ---------------------------------------------------------------------------

def test_lancamento_grava_e_aparece_na_tela(cliente):
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=30)

    resposta = cliente.post("/api/marco", json={
        "escala_id": escala_id, "tipo": "arrival",
        "hora_local": "2026-03-01T07:00", "offset": "-03:00",
        "nome_responsavel": "Cmt. Almeida", "id_cliente": "cli-1",
    })
    assert resposta.status_code == 200 and resposta.json()["ok"] is True
    assert "07:00" in cliente.get("/navio").text
    assert "Cmt. Almeida" in cliente.get("/navio").text


def test_reenvio_do_mesmo_id_nao_duplica(cliente):
    """A fila do celular repete o envio quando o servidor demora a acordar."""
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=30)
    corpo = {"escala_id": escala_id, "tipo": "arrival",
             "hora_local": "2026-03-01T07:00", "offset": "-03:00",
             "nome_responsavel": "Cmt. Almeida", "id_cliente": "cli-repetido"}

    primeiro = cliente.post("/api/marco", json=corpo).json()
    segundo = cliente.post("/api/marco", json=corpo).json()
    assert primeiro["evento_id"] == segundo["evento_id"]

    with closing(db.conectar()) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evento WHERE escala_id = ?", (escala_id,)
        ).fetchone()[0] == 1


def test_dado_invalido_devolve_422_para_a_fila_parar_de_tentar(cliente):
    """422 e o sinal de 'nao adianta reenviar'. Sem ele a fila repete para sempre."""
    entrar(cliente)
    cliente.get("/navio")
    barra = escala_de(cliente, ordem=50)      # escala de passagem: nao tem berth

    resposta = cliente.post("/api/marco", json={
        "escala_id": barra, "tipo": "berth",
        "hora_local": "2026-03-01T07:00", "offset": "-03:00",
        "nome_responsavel": "Cmt. Almeida", "id_cliente": "cli-2"})
    assert resposta.status_code == 422
    assert any("não atraca aqui" in e for e in resposta.json()["erros"])


def test_navio_nao_lanca_em_escala_de_outro_navio(cliente):
    entrar(cliente, "navio.pioneer")
    cliente.get("/navio")                      # abre a viagem do Pioneer
    cliente.get("/logout")
    entrar(cliente, "navio.pathfinder")
    cliente.get("/navio")

    with closing(db.conectar()) as conn:
        alheia = conn.execute(
            "SELECT e.id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE vg.navio_id = 2 LIMIT 1").fetchone()[0]

    resposta = cliente.post("/api/marco", json={
        "escala_id": alheia, "tipo": "arrival", "hora_local": "2026-03-01T07:00",
        "offset": "-03:00", "nome_responsavel": "X", "id_cliente": "cli-3"})
    assert resposta.status_code == 403


def test_correcao_pela_api_exige_motivo(cliente):
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=30)
    base = {"escala_id": escala_id, "tipo": "arrival", "offset": "-03:00",
            "nome_responsavel": "Cmt. Almeida"}

    cliente.post("/api/marco", json=dict(base, hora_local="2026-03-01T07:00",
                                         id_cliente="c1"))
    sem_motivo = cliente.post("/api/marco", json=dict(base, hora_local="2026-03-01T07:45",
                                                      id_cliente="c2"))
    assert sem_motivo.status_code == 422

    com_motivo = cliente.post("/api/marco", json=dict(
        base, hora_local="2026-03-01T07:45", id_cliente="c3",
        motivo_correcao="conferido no diario"))
    assert com_motivo.status_code == 200
    assert "corrigido" in cliente.get("/navio").text


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------

def test_tela_nao_oferece_marco_que_a_escala_nao_tem(cliente):
    """Barra Norte e passagem: a tela nao pode nem mostrar Berth para preencher."""
    import re

    entrar(cliente)
    html = cliente.get("/navio").text       # abre a viagem, se nao houver
    barra = escala_de(cliente, ordem=50)
    bloco = re.findall(
        r'data-escala="{}" data-tipo="([a-z]+)"'.format(barra), html)
    assert set(bloco) == {"arrival", "sailing"}


def test_tela_mostra_o_codigo_e_onde_o_navio_esta(cliente):
    from app.db import agora

    entrar(cliente)
    html = cliente.get("/navio").text
    assert "APT{}001".format(agora()[2:4]) in html
    assert 'class="faixa"' in html               # a regua que substituiu o cartao
    assert "Viagem nova" in html                 # nada lancado ainda


def _parada_aberta(cliente):
    """O porto da parada que vem aberta — a que substituiu o cartao."""
    import re
    html = cliente.get("/navio").text
    aberta = re.search(r'<details class="escala[^>]*\sopen>(.*?)</summary>', html, re.S)
    if aberta is None:
        return None
    nome = re.search(r'<span class="porto">([^<]+)', aberta.group(1))
    return nome.group(1).strip() if nome else None


def test_uma_acao_obvia_por_vez(cliente):
    """O paredao de quinze formularios abertos foi o que motivou este desenho.

    A regra sobreviveu ao fim do cartao de "proximo lancamento": agora e UMA
    parada que vem aberta, e so ela. O resto continua fechado.
    """
    entrar(cliente)
    html = cliente.get("/navio").text
    assert html.count(" open>") == 1
    assert _parada_aberta(cliente) == "Alumar"


def test_o_cartao_de_proximo_lancamento_nao_volta(cliente):
    """Ele repetia num retangulo a parte a mesma parada que ja estava na lista."""
    entrar(cliente)
    html = cliente.get("/navio").text
    for sumido in ('class="proximo"', "proximo-rot", "proximo-titulo"):
        assert sumido not in html, sumido


def test_a_parada_aberta_avanca_conforme_a_viagem(cliente):
    """E o que faz a viagem 'se traduzir': a lista anda sozinha."""
    entrar(cliente)
    cliente.get("/navio")
    assert _parada_aberta(cliente) == "Alumar"     # a saida que abre a viagem

    abertura = escala_de(cliente, ordem=10)
    cliente.post("/api/marco", json={
        "escala_id": abertura, "tipo": "sailing", "hora_local": "2026-03-01T10:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "av-1"})

    assert _parada_aberta(cliente) == "Fazendinha"  # a lista andou


def test_tabela_mostra_o_progresso_de_cada_parada(cliente):
    entrar(cliente)
    cliente.get("/navio")
    juruti = escala_de(cliente, ordem=30)
    cliente.post("/api/marco", json={
        "escala_id": juruti, "tipo": "arrival", "hora_local": "2026-03-02T08:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "tr-1"})

    html = cliente.get("/navio").text
    assert 'class="tabela"' in html
    assert "1 de 4" in html                       # por extenso, nao fracao solta
    assert 'class="escala parcial' in html
    # Um marco so nao delimita janela: mostrar "0h00" sugeriria escala instantanea.
    assert "0h00" not in html


def test_lancamento_acontece_na_propria_tela(cliente):
    """Sem trocar de pagina: o formulario de cada marco vive na lista."""
    entrar(cliente)
    html = cliente.get("/navio").text
    assert "form class=\"lancar\"" in html
    assert "/static/escalas.js" in html
    assert "/navio/marco" not in html


def test_api_avisa_quando_a_viagem_troca(cliente):
    """O unberth de Alumar fecha a viagem e abre outra — a tela recarrega."""
    entrar(cliente)
    cliente.get("/navio")
    alumar = escala_de(cliente, ordem=60)
    base = {"escala_id": alumar, "offset": "-03:00", "nome_responsavel": "Cmt."}

    for i, (tipo, hora) in enumerate((("arrival", "2026-03-01T08:00"),
                                      ("berth", "2026-03-02T08:00"))):
        r = cliente.post("/api/marco", json=dict(base, tipo=tipo, hora_local=hora,
                                                 id_cliente="a{}".format(i)))
        assert r.status_code == 200, r.text
        assert r.json()["viagem_mudou"] is False

    # o UNBERTH e o ultimo lancamento: fecha esta viagem e abre a seguinte
    fim = cliente.post("/api/marco", json=dict(
        base, tipo="unberth", hora_local="2026-03-03T08:00", id_cliente="a9"))
    assert fim.status_code == 200
    assert fim.json()["viagem_mudou"] is True


def test_escala_extra_pela_tela(cliente):
    entrar(cliente)
    cliente.get("/navio")
    resposta = cliente.post("/navio/escala-extra", data={
        "codigo_porto": "ICOARACI", "motivo": "bunker",
        "apos_ordem": 10, "tipo_escala": "fundeio"}, follow_redirects=False)
    assert resposta.status_code == 303
    pagina = cliente.get("/navio").text
    assert "Icoaraci" in pagina and "Parada adicional" in pagina


def test_estatico_e_publico(cliente):
    for arquivo in ("/static/estilo.css", "/static/fila.js", "/static/escalas.js"):
        assert cliente.get(arquivo).status_code == 200


# ---------------------------------------------------------------------------
# Administracao de contas — o Render free nao tem terminal
# ---------------------------------------------------------------------------

def test_admin_de_arranque_nasce_do_ambiente(monkeypatch):
    """Sem isto o sistema sobe em producao e ninguem consegue entrar."""
    caminho = pathlib.Path(os.environ["SHIPOPS_BANCO"])
    if caminho.exists():
        caminho.unlink()
    monkeypatch.setattr(main.config, "ADMIN_LOGIN", "chefe")
    monkeypatch.setattr(main.config, "ADMIN_SENHA", "senha-inicial-123")

    with TestClient(main.app) as c:
        resposta = c.post("/login", data={"login": "chefe", "senha": "senha-inicial-123"},
                          follow_redirects=False)
        assert resposta.status_code == 303 and "erro=" not in resposta.headers["location"]
        assert c.get("/admin/contas").status_code == 200


def test_admin_de_arranque_realinha_a_senha(monkeypatch):
    """O caminho de recuperacao: troca-se a variavel e redeploya."""
    caminho = pathlib.Path(os.environ["SHIPOPS_BANCO"])
    if caminho.exists():
        caminho.unlink()
    monkeypatch.setattr(main.config, "ADMIN_LOGIN", "chefe")
    monkeypatch.setattr(main.config, "ADMIN_SENHA", "senha-antiga-123")
    with TestClient(main.app):
        pass

    monkeypatch.setattr(main.config, "ADMIN_SENHA", "senha-nova-456")
    with TestClient(main.app) as c:
        velha = c.post("/login", data={"login": "chefe", "senha": "senha-antiga-123"},
                       follow_redirects=False)
        assert "erro=" in velha.headers["location"]
        nova = c.post("/login", data={"login": "chefe", "senha": "senha-nova-456"},
                      follow_redirects=False)
        assert "erro=" not in nova.headers["location"]


def test_sem_variavel_de_admin_nenhuma_conta_e_criada(cliente):
    with closing(db.conectar()) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM conta WHERE perfil = 'admin'").fetchone()[0] == 0


def test_nao_admin_nao_entra_na_tela_de_contas(cliente):
    entrar(cliente, "vlo")            # supervisor
    assert cliente.get("/admin/contas").status_code == 403
    entrar(cliente, "navio.pathfinder")
    assert cliente.get("/admin/contas").status_code == 403


def test_admin_cria_conta_de_navio_pela_tela(cliente, monkeypatch):
    with closing(db.conectar()) as conn:
        contas.criar_conta(conn, login="chefe", senha=SENHA,
                           nome_exibicao="Chefe", perfil="admin")
    entrar(cliente, "chefe")

    resposta = cliente.post("/admin/contas", data={
        "login": "navio.courage", "nome_exibicao": "Amazon Courage",
        "perfil": "navio", "senha": "outra-senha-123", "navio_id": "4"},
        follow_redirects=False)
    assert resposta.status_code == 303 and "ok=" in resposta.headers["location"]

    cliente.get("/logout")
    entrada = cliente.post("/login", data={"login": "navio.courage",
                                           "senha": "outra-senha-123"},
                           follow_redirects=False)
    assert "erro=" not in entrada.headers["location"]


def test_conta_de_navio_sem_navio_e_recusada(cliente):
    with closing(db.conectar()) as conn:
        contas.criar_conta(conn, login="chefe", senha=SENHA,
                           nome_exibicao="Chefe", perfil="admin")
    entrar(cliente, "chefe")
    resposta = cliente.post("/admin/contas", data={
        "login": "navio.solto", "nome_exibicao": "Solto", "perfil": "navio",
        "senha": "senha-boa-123", "navio_id": ""}, follow_redirects=False)
    assert "erro=" in resposta.headers["location"]
    assert "vinculada%20a%20um%20navio" in resposta.headers["location"]


def test_recusa_subir_em_hospedagem_efemera_sem_turso(monkeypatch):
    """Sem esta trava o dado sumiria no primeiro redeploy, em silencio."""
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError, match="TURSO_DATABASE_URL"):
        with TestClient(main.app):
            pass


def test_fora_do_render_o_sqlite_local_e_aceito(cliente):
    assert cliente.get("/api/health").json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Condicao, combustivel e portos colapsaveis
# ---------------------------------------------------------------------------

def test_portos_sao_colapsaveis_e_mostram_a_condicao(cliente):
    entrar(cliente)
    html = cliente.get("/navio").text
    assert html.count('<details class="escala') >= 6      # cada porto fecha
    for condicao in ("ballast", "loading", "laden", "discharging"):
        assert '>{}</span>'.format(condicao) in html


def test_formulario_pede_combustivel_a_bordo(cliente):
    entrar(cliente)
    html = cliente.get("/navio").text
    assert 'name="rob_vlsfo"' in html
    assert 'name="rob_mgo"' in html


def test_rob_chega_pela_api_e_aparece_na_tela(cliente):
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=30)
    resposta = cliente.post("/api/marco", json={
        "escala_id": escala_id, "tipo": "arrival", "hora_local": "2026-03-01T07:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "rob-1",
        "rob_vlsfo": "512.25", "rob_mgo": "40"})
    assert resposta.status_code == 200
    html = cliente.get("/navio").text
    assert "512.25" in html and "VLSFO" in html


def test_rob_invalido_devolve_422(cliente):
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=30)
    resposta = cliente.post("/api/marco", json={
        "escala_id": escala_id, "tipo": "arrival", "hora_local": "2026-03-01T07:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "rob-2",
        "rob_vlsfo": "meio tanque"})
    assert resposta.status_code == 422


def test_abastecimento_pela_api(cliente):
    entrar(cliente)
    cliente.get("/navio")
    with closing(db.conectar()) as conn:
        viagem_id = conn.execute(
            "SELECT id FROM viagem WHERE status = 'aberta' LIMIT 1").fetchone()[0]
    cliente.post("/navio/escala-extra", data={
        "codigo_porto": "ICOARACI", "motivo": "bunker",
        "apos_ordem": 10, "tipo_escala": "fundeio"})
    with closing(db.conectar()) as conn:
        bunker = conn.execute(
            "SELECT id FROM escala WHERE viagem_id = ? AND codigo_porto = 'ICOARACI'",
            (viagem_id,)).fetchone()[0]

    resposta = cliente.post("/api/abastecimento", json={
        "escala_id": bunker, "vlsfo": "220", "mgo": "15", "nome_responsavel": "Cmt."})
    assert resposta.status_code == 200
    with closing(db.conectar()) as conn:
        linha = conn.execute(
            "SELECT vlsfo, mgo FROM abastecimento WHERE escala_id = ?", (bunker,)).fetchone()
    assert linha["vlsfo"] == 220.0 and linha["mgo"] == 15.0
    assert 'class="abastecer"' in cliente.get("/navio").text


def test_abastecimento_de_outro_navio_e_bloqueado(cliente):
    entrar(cliente, "navio.pioneer")
    cliente.get("/navio")
    cliente.get("/logout")
    entrar(cliente, "navio.pathfinder")
    cliente.get("/navio")
    with closing(db.conectar()) as conn:
        alheia = conn.execute(
            "SELECT e.id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE vg.navio_id = 2 LIMIT 1").fetchone()[0]
    resposta = cliente.post("/api/abastecimento", json={
        "escala_id": alheia, "vlsfo": "100", "nome_responsavel": "X"})
    assert resposta.status_code == 403


def test_so_escala_de_bunker_pede_quantidade(cliente):
    entrar(cliente)
    html = cliente.get("/navio").text
    assert 'class="abastecer"' not in html      # a rota padrao nao tem bunker


# ---------------------------------------------------------------------------
# Carga, datas brasileiras e tres casas
# ---------------------------------------------------------------------------

def test_datas_aparecem_no_formato_brasileiro(cliente):
    entrar(cliente)
    cliente.get("/navio")
    escala_id = escala_de(cliente, ordem=30)
    cliente.post("/api/marco", json={
        "escala_id": escala_id, "tipo": "arrival", "hora_local": "2026-08-21T04:20",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "br-1",
        "rob_vlsfo": "486.2", "rob_mgo": "37.5"})

    import re

    html = cliente.get("/navio").text
    assert "21/08/2026 04:20" in html
    assert "486,200" in html                 # tres casas, padrao brasileiro
    assert "37,500" in html

    # O ISO so pode aparecer no value de <input type="date"> — a especificacao
    # HTML exige yyyy-mm-dd ali, e o navegador exibe no formato do aparelho.
    # Em texto visivel, nunca.
    visivel = re.sub(r"<input[^>]*>", "", html)
    assert "2026-08-21" not in visivel


def test_so_quem_carrega_ou_descarrega_pede_quantidade(cliente):
    import re

    entrar(cliente)
    html = cliente.get("/navio").text
    caixas = re.findall(r'data-escala="(\d+)" data-tipo="carga"', html)
    assert len(caixas) == 2                  # Juruti e Alumar, so eles
    assert "Carregado" in html and "Descarregado" in html


def test_quantidade_de_carga_pela_api(cliente):
    entrar(cliente)
    cliente.get("/navio")
    juruti = escala_de(cliente, ordem=30)

    resposta = cliente.post("/api/carga", json={
        "escala_id": juruti, "quantidade": "58000", "nome_responsavel": "Cmt."})
    assert resposta.status_code == 200
    with closing(db.conectar()) as conn:
        linha = conn.execute(
            "SELECT carregado, descarregado FROM movimento_carga WHERE escala_id = ?",
            (juruti,)).fetchone()
    assert linha["carregado"] == 58000.0 and linha["descarregado"] is None
    assert "58000" in cliente.get("/navio").text


def test_carga_em_escala_que_nao_movimenta_devolve_422(cliente):
    entrar(cliente)
    cliente.get("/navio")
    barra = escala_de(cliente, ordem=50)
    resposta = cliente.post("/api/carga", json={
        "escala_id": barra, "quantidade": "100", "nome_responsavel": "Cmt."})
    assert resposta.status_code == 422
    assert any("não movimenta carga" in e for e in resposta.json()["erros"])


def test_carga_de_outro_navio_e_bloqueada(cliente):
    entrar(cliente, "navio.pioneer")
    cliente.get("/navio")
    cliente.get("/logout")
    entrar(cliente, "navio.pathfinder")
    cliente.get("/navio")
    with closing(db.conectar()) as conn:
        alheia = conn.execute(
            "SELECT e.id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE vg.navio_id = 2 AND e.ordem = 30").fetchone()[0]
    resposta = cliente.post("/api/carga", json={
        "escala_id": alheia, "quantidade": "100", "nome_responsavel": "X"})
    assert resposta.status_code == 403


# ---------------------------------------------------------------------------
# O desenho aprovado em 10/set/2026
# ---------------------------------------------------------------------------

def test_sailing_de_abertura_aparece_no_cabecalho(cliente):
    """Era o que faltava: o começo da história, antes invisível."""
    entrar(cliente)
    cliente.get("/navio")
    saida = escala_de(cliente, ordem=10)
    cliente.post("/api/marco", json={
        "escala_id": saida, "tipo": "sailing", "hora_local": "2026-08-16T14:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "ab-1"})

    html = cliente.get("/navio").text
    assert "Saiu de Alumar" in html
    assert "16/08/2026 14:00" in html


def test_a_linha_mostra_a_janela_da_escala(cliente):
    """O ganho da tabela: ver a viagem sem abrir parada nenhuma."""
    entrar(cliente)
    cliente.get("/navio")
    juruti = escala_de(cliente, ordem=30)
    for i, (tipo, hora) in enumerate((("arrival", "2026-08-21T04:20"),
                                      ("berth", "2026-08-21T14:00"))):
        cliente.post("/api/marco", json={
            "escala_id": juruti, "tipo": tipo, "hora_local": hora,
            "offset": "-03:00", "nome_responsavel": "Cmt.",
            "id_cliente": "res-{}".format(i)})

    # Com 2 de 4, a escala nao acabou: mostrar o ultimo marco na coluna FIM
    # leria como escala encerrada, com o navio ainda atracado.
    html = cliente.get("/navio").text
    assert "21/08/2026 04:20" in html      # o inicio ja aparece
    assert "em curso" in html
    assert "2 de 4" in html
    assert "9h40" not in html              # duracao so quando fecha

    for i, (tipo, hora) in enumerate((("unberth", "2026-08-22T09:30"),
                                      ("sailing", "2026-08-22T11:00"))):
        cliente.post("/api/marco", json={
            "escala_id": juruti, "tipo": tipo, "hora_local": hora,
            "offset": "-03:00", "nome_responsavel": "Cmt.",
            "id_cliente": "fim-{}".format(i)})

    # Fechada: do PRIMEIRO ao ULTIMO lancamento, e quanto durou.
    html = cliente.get("/navio").text
    assert "21/08/2026 04:20" in html and "22/08/2026 11:00" in html
    assert "30h40" in html                 # 21/08 04:20 -> 22/08 11:00


def test_quem_preenche_fica_na_barra_lateral_e_so_uma_vez(cliente):
    """Dois campos com o mesmo id fariam o JavaScript ler o errado."""
    entrar(cliente)
    html = cliente.get("/navio").text
    assert html.count('id="responsavel"') == 1
    assert html.index('id="responsavel"') < html.index('class="painel"')


def test_a_tela_usa_a_grade_do_computador(cliente):
    """Os quatro blocos sao IRMAOS: e o que deixa o celular ler na ordem do
    HTML e o computador reposicionar pela grade. Aninhar em colunas quebraria
    uma das duas leituras."""
    entrar(cliente)
    html = cliente.get("/navio").text
    for marca in ('class="tela"', 'class="faixa"', 'class="bloco-viagem"',
                  'class="tabela"', 'class="cabecalho"'):
        assert marca in html, marca
    assert 'class="coluna-lado"' not in html
    # o teto de largura vive no base.html, uma vez, para todas as telas
    assert 'class="limite"' in html


def test_o_topo_mostra_o_quanto_da_viagem_ja_foi_lancado(cliente):
    """<progress> e nao <div>: a CSP proibe `style=`, entao nao ha como
    escrever a largura da barra no HTML."""
    entrar(cliente)
    html = cliente.get("/navio").text
    assert '<progress class="barra"' in html
    assert 'max="14"' in html


def test_nenhum_template_usa_style_inline(cliente):
    """A CSP bloqueia `style=` EM SILENCIO — o atributo e ignorado e o espaco
    simplesmente nao aparece. Sem este teste a regressao passa despercebida."""
    import pathlib as _p
    raiz = _p.Path(__file__).resolve().parent.parent / "templates"
    culpados = [str(a.name) for a in raiz.glob("*.html")
                if 'style="' in a.read_text(encoding="utf-8")]
    assert not culpados, culpados


# ---------------------------------------------------------------------------
# Cache do CSS e do JS
#
# O Vinicius abriu o sistema depois do deploy e viu a tela ANTERIOR ao
# redesenho: o navegador dele tinha `/static/estilo.css` guardado e nao foi
# buscar de novo. A bordo isso e pior — ninguem vai ensinar comandante a
# limpar cache. Estes testes guardam a correcao.
# ---------------------------------------------------------------------------

def test_links_estaticos_carregam_impressao_digital(cliente):
    """Arquivo novo tem que ter endereco novo, senao o navegador nao rebusca."""
    entrar(cliente)
    html = cliente.get("/navio").text
    for arquivo in ("estilo.css", "fila.js", "escalas.js"):
        assert "/static/{}?v=".format(arquivo) in html, arquivo
    # e a impressao digital tem que estar preenchida, nao vazia
    assert "?v=\"" not in html and "?v='" not in html


def test_impressao_digital_muda_quando_o_arquivo_muda(tmp_path, monkeypatch):
    """Se o digest nao mudar com o conteudo, o endereco congela e o bug volta."""
    from app import main

    pasta = tmp_path / "static"
    pasta.mkdir()
    (pasta / "estilo.css").write_text("body{color:red}", encoding="utf-8")
    monkeypatch.setattr(main, "ESTATICOS", pasta)
    antes = main.versao_estaticos()

    (pasta / "estilo.css").write_text("body{color:blue}", encoding="utf-8")
    assert main.versao_estaticos() != antes


def test_estatico_pode_ser_guardado_para_sempre_e_o_html_nunca(cliente):
    """O par que faz a coisa funcionar: endereco versionado + guardar a vontade.

    O HTML tem que ser `no-store` — e ele que carrega o endereco novo. Se o
    HTML fosse guardado, o navegador continuaria pedindo a versao velha do CSS.
    """
    entrar(cliente)
    estatico = cliente.get("/static/estilo.css")
    assert "immutable" in estatico.headers.get("cache-control", "")

    pagina = cliente.get("/navio")
    assert pagina.headers.get("cache-control") == "no-store"


# ---------------------------------------------------------------------------
# Salvar sem recarregar a pagina
#
# Antes, cada lancamento do cartao "proximo lancamento" terminava em
# `location.reload()`. Numa maquina que dormiu no Render isso e a tela em
# branco por 20 a 50 segundos logo depois do clique em Salvar — parece
# travamento, e o formulario reaparece preenchido como se nada tivesse
# sido gravado. Agora o servidor devolve so o miolo da tela.
# ---------------------------------------------------------------------------

def test_a_tela_vive_numa_caixa_que_o_javascript_sabe_trocar(cliente):
    entrar(cliente)
    html = cliente.get("/navio").text
    assert 'id="tela-viagem"' in html


def test_o_fragmento_e_so_o_miolo_da_tela(cliente):
    """Se voltasse a pagina inteira, a troca aninharia <html> dentro do corpo."""
    entrar(cliente)
    fragmento = cliente.get("/navio/tela")
    assert fragmento.status_code == 200
    corpo = fragmento.text
    assert 'class="tela"' in corpo and 'class="tabela"' in corpo
    for fora in ("<!doctype", "<html", "<body", 'class="lateral"', "/static/estilo.css"):
        assert fora not in corpo.lower(), fora


def test_o_fragmento_ja_vem_com_o_lancamento_que_acabou_de_entrar(cliente):
    """E o que substitui o reload: o cartao avanca e a contagem sobe."""
    entrar(cliente)
    cliente.get("/navio")
    alumar = escala_de(cliente, ordem=10)

    antes = cliente.get("/navio/tela").text
    assert "0 de 14 marcos" in antes

    cliente.post("/api/marco", json={
        "escala_id": alumar, "tipo": "sailing", "hora_local": "2026-08-20T18:40",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "frag-1"})

    depois = cliente.get("/navio/tela").text
    assert "1 de 14 marcos" in depois
    assert "20/08/2026 18:40" in depois


def test_o_fragmento_e_so_do_comandante(cliente):
    entrar(cliente, login="vlo")
    assert cliente.get("/navio/tela").status_code == 403


def test_o_javascript_so_recarrega_quando_nao_ha_alternativa(cliente):
    """Guarda de regressao.

    DOIS `location.reload()` sao legitimos, e so eles:

    1. Sessao caida ou viagem trocada — a pagina inteira mudou de forma.
    2. Correcao em viagem encerrada (`data-recarrega`) — mexe no RESUMO da
       viagem, nao so na linha, e ali nao ha miolo a trocar.

    O caminho do dia a dia — lancar marco no painel — nao pode ter nenhum. Se
    esta contagem subir, e sinal de que voltou, e o comandante vai sentir a
    tela travar de novo.
    """
    import re
    js = (pathlib.Path(__file__).resolve().parent.parent
          / "app" / "static" / "escalas.js").read_text(encoding="utf-8")
    # sem os comentarios: o proprio arquivo EXPLICA o reload que foi tirado,
    # e contar a explicacao junto com o codigo esconderia a regressao
    codigo = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", js, flags=re.S))
    assert codigo.count("location.reload()") == 2, codigo.count("location.reload()")
    assert "data-recarrega" not in codigo  # o JavaScript le dataset.recarrega
    assert "/navio/tela" in codigo


# ---------------------------------------------------------------------------
# Remover uma parada acrescentada por engano
# ---------------------------------------------------------------------------

def _extra(cliente, motivo="bunker", porto="ICOARACI"):
    cliente.post("/navio/escala-extra", data={
        "codigo_porto": porto, "motivo": motivo, "apos_ordem": 10,
        "tipo_escala": "fundeio"}, follow_redirects=False)
    with closing(db.conectar()) as conn:
        return conn.execute(
            "SELECT e.id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE e.origem = 'extra' AND vg.status = 'aberta' "
            " ORDER BY e.id DESC LIMIT 1").fetchone()[0]


def _tabela(cliente):
    """So a tabela de paradas.

    O nome do porto tambem aparece no seletor de "acrescentar parada", entao
    procurar no HTML inteiro acharia Icoaraci mesmo depois de ela sair da viagem.
    """
    html = cliente.get("/navio/tela").text
    return html[html.index('<div class="tabela">'):html.index('class="extra"')]


def _remover(cliente, escala_id):
    return cliente.post("/navio/escala-extra/remover",
                        data={"escala_id": escala_id}, follow_redirects=False)


def test_parada_extra_vazia_pode_ser_removida(cliente):
    entrar(cliente)
    cliente.get("/navio")
    extra = _extra(cliente)
    assert 'class="remover"' in cliente.get("/navio").text

    resposta = _remover(cliente, extra)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/navio"
    with closing(db.conectar()) as conn:
        assert conn.execute("SELECT 1 FROM escala WHERE id = ?",
                            (extra,)).fetchone() is None


def test_parada_do_circuito_padrao_nao_sai(cliente):
    """As seis definem a viagem: sem Juruti nao ha carregamento."""
    entrar(cliente)
    cliente.get("/navio")
    juruti = escala_de(cliente, ordem=30)

    resposta = _remover(cliente, juruti)
    assert "circuito%20padr" in resposta.headers["location"]
    with closing(db.conectar()) as conn:
        assert conn.execute("SELECT 1 FROM escala WHERE id = ?", (juruti,)).fetchone()


def test_parada_extra_com_lancamento_sai_da_tela_sem_perder_o_lancamento(cliente):
    """O que se desfaz e a PARADA, nao o lancamento.

    O comandante acrescentou Icoaraci e ela nao devia estar ali — some, mesmo
    ja tendo marco dentro. Mas este sistema nunca destroi o que alguem afirmou:
    a escala fica CANCELADA e o evento continua no banco, preso a ela.
    """
    entrar(cliente)
    cliente.get("/navio")
    extra = _extra(cliente)
    cliente.post("/api/marco", json={
        "escala_id": extra, "tipo": "arrival", "hora_local": "2026-08-20T09:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "extra-1"})
    assert "Icoaraci" in _tabela(cliente)

    resposta = _remover(cliente, extra)
    assert resposta.headers["location"] == "/navio"

    # sumiu da trilha do comandante...
    assert "Icoaraci" not in _tabela(cliente)
    with closing(db.conectar()) as conn:
        assert conn.execute(
            "SELECT status FROM escala WHERE id = ?", (extra,)).fetchone()[0] == "cancelada"
        # ...e o que ele digitou continua la
        assert conn.execute(
            "SELECT COUNT(*) FROM evento WHERE escala_id = ?", (extra,)).fetchone()[0] == 1
        # ...sem cobrar nada nas filas
        assert conn.execute(
            "SELECT COUNT(*) FROM escalas_incompletas WHERE escala_id = ?",
            (extra,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM escalas_a_conferir WHERE escala_id = ?",
            (extra,)).fetchone()[0] == 0


def test_remover_a_mesma_parada_duas_vezes_nao_e_erro(cliente):
    """O comandante pode clicar de novo sem entender por que nao sumiu antes."""
    entrar(cliente)
    cliente.get("/navio")
    extra = _extra(cliente)
    cliente.post("/api/marco", json={
        "escala_id": extra, "tipo": "arrival", "hora_local": "2026-08-20T09:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "extra-2"})
    _remover(cliente, extra)
    assert _remover(cliente, extra).headers["location"] == "/navio"


def test_um_navio_nao_remove_parada_do_outro(cliente):
    """O escala_id vem do formulario: sem esta conferencia, trocar o numero
    bastaria para mexer na viagem do navio ao lado."""
    entrar(cliente, login="navio.pioneer")
    cliente.get("/navio")
    do_pioneer = _extra(cliente)

    cliente.get("/logout")
    entrar(cliente, login="navio.pathfinder")
    resposta = _remover(cliente, do_pioneer)
    assert "encontrada" in resposta.headers["location"]
    with closing(db.conectar()) as conn:
        assert conn.execute("SELECT 1 FROM escala WHERE id = ?",
                            (do_pioneer,)).fetchone()


def test_abertura_esta_completa_e_mesmo_assim_nao_tem_fim(cliente):
    """A saída de Alumar tem UM marco só.

    Perguntar pelo fim antes de perguntar se a escala fechou fazia a linha dizer
    "em curso" e "completa" na mesma linha — duas afirmações contraditórias
    sobre a mesma escala.
    """
    entrar(cliente)
    cliente.get("/navio")
    abertura = escala_de(cliente, ordem=10)
    cliente.post("/api/marco", json={
        "escala_id": abertura, "tipo": "sailing", "hora_local": "2026-03-01T10:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "ab-1"})

    linha = _tabela(cliente)
    alumar = linha[:linha.index("</details>")]
    assert "01/03/2026 10:00" in alumar     # o início está lá
    assert "completa" in alumar
    assert "em curso" not in alumar


# ---------------------------------------------------------------------------
# Viagens encerradas
#
# Tela separada do painel porque as duas respondem perguntas diferentes: o
# painel diz "o que lanço agora", esta diz "o que aconteceu".
# ---------------------------------------------------------------------------

VIAGEM_INTEIRA = (
    (10, "sailing", "2026-03-01T18:40", 1421.5, 112.25),
    (20, "arrival", "2026-03-02T09:15", 1388.0, 110.1),
    (20, "sailing", "2026-03-02T10:05", 1386.2, 110.0),
    (30, "arrival", "2026-03-03T22:30", 1301.75, 99.5),
    (30, "berth", "2026-03-04T06:10", 1284.5, 98.2),
    (30, "unberth", "2026-03-05T14:20", 1276.1, 96.4),
    (30, "sailing", "2026-03-05T16:05", 1274.8, 96.1),
    (40, "arrival", "2026-03-06T20:40", 1208.3, 90.7),
    (40, "sailing", "2026-03-06T21:25", 1206.9, 90.5),
    (50, "arrival", "2026-03-07T04:10", 1190.4, 88.9),
    (50, "sailing", "2026-03-07T11:50", 1188.1, 88.6),
    (60, "arrival", "2026-03-07T19:30", 1171.6, 87.0),
    (60, "berth", "2026-03-07T23:50", 1169.9, 86.7),
    (60, "unberth", "2026-03-08T07:55", 1166.2, 85.9),
)


def _percorrer(cliente, marcos=VIAGEM_INTEIRA, prefixo="ok", carga=True):
    """Lança uma viagem inteira; o unberth de Alumar a fecha e abre a próxima."""
    cliente.get("/navio")
    with closing(db.conectar()) as conn:
        ids = {e["ordem"]: e["id"] for e in conn.execute(
            "SELECT e.ordem, e.id FROM escala e JOIN viagem v ON v.id = e.viagem_id"
            " WHERE v.status = 'aberta' AND v.navio_id ="
            " (SELECT navio_id FROM viagem WHERE status = 'aberta' ORDER BY id LIMIT 1)"
        )}
    if carga:
        cliente.post("/api/carga", json={
            "escala_id": ids[30], "quantidade": 58000, "nome_responsavel": "Cmt.",
            "id_cliente": "{}-carga-sobe".format(prefixo)})
    for i, (ordem, tipo, hora, vl, mg) in enumerate(marcos):
        cliente.post("/api/marco", json={
            "escala_id": ids[ordem], "tipo": tipo, "hora_local": hora,
            "offset": "-03:00", "nome_responsavel": "Cmte. Andrade",
            "rob_vlsfo": vl, "rob_mgo": mg,
            "id_cliente": "{}-{}".format(prefixo, i)})
        if ordem == 60 and tipo == "arrival" and carga:
            cliente.post("/api/carga", json={
                "escala_id": ids[60], "quantidade": 57500, "nome_responsavel": "Cmt.",
                "id_cliente": "{}-carga-desce".format(prefixo)})
    return ids


def test_encerradas_lista_o_que_se_procura_numa_consulta(cliente):
    entrar(cliente)
    _percorrer(cliente)

    html = cliente.get("/navio/encerradas").text
    assert "APT26001" in html
    assert "01/03/2026 18:40" in html       # saída de Alumar
    assert "08/03/2026 07:55" in html       # encerrada em
    assert "6d 13h" in html                 # duração da viagem
    assert "57.500,000" in html             # carga descarregada
    assert "completa" in html


def test_encerradas_denuncia_a_viagem_com_marco_faltando(cliente):
    """O Unberth de Alumar fecha a viagem mesmo com um Arrival do meio vazio.

    Sem a coluna de situação esse buraco ficaria invisível para sempre.
    """
    entrar(cliente)
    sem_o_arrival = tuple(m for m in VIAGEM_INTEIRA
                          if not (m[0] == 40 and m[1] == "arrival"))
    _percorrer(cliente, sem_o_arrival, prefixo="furo")

    html = cliente.get("/navio/encerradas").text
    assert "1 marco faltando" in html
    assert "não lançado" in html


def test_a_busca_encontra_por_codigo_e_por_porto(cliente):
    entrar(cliente)
    _percorrer(cliente)

    assert "APT26001" in cliente.get("/navio/encerradas?busca=26001").text
    assert "APT26001" in cliente.get("/navio/encerradas?busca=juruti").text
    vazio = cliente.get("/navio/encerradas?busca=santos").text
    assert "APT26001" not in vazio
    assert "Nenhuma viagem encerrada corresponde" in vazio


def test_o_filtro_de_ano_usa_a_saida_de_alumar(cliente):
    entrar(cliente)
    _percorrer(cliente)

    assert "APT26001" in cliente.get("/navio/encerradas?ano=2026").text
    assert "APT26001" not in cliente.get("/navio/encerradas?ano=2025").text


def test_corrigir_o_unberth_de_alumar_move_a_ancora_da_viagem(cliente):
    """Sem mover a âncora, a viagem guardaria para sempre a hora errada.

    `evento_encerramento_id` aponta para o evento que a fechou. A correção cria
    uma VERSÃO nova e aposenta a anterior — se a âncora ficasse na aposentada, a
    duração da viagem sairia da versão que ninguém mais considera vigente.
    """
    entrar(cliente)
    ids = _percorrer(cliente)

    assert "6d 13h" in cliente.get("/navio/encerradas").text

    cliente.post("/api/marco", json={
        "escala_id": ids[60], "tipo": "unberth", "hora_local": "2026-03-08T15:55",
        "offset": "-03:00", "nome_responsavel": "Cmte. Andrade",
        "motivo_correcao": "hora conferida no diário de bordo",
        "id_cliente": "corrige-unberth"})

    html = cliente.get("/navio/encerradas").text
    assert "08/03/2026 15:55" in html      # a versão nova
    assert "6d 21h" in html                # e a duração acompanhou
    assert "corrigido" in html

    with closing(db.conectar()) as conn:
        vigente = conn.execute(
            "SELECT ev.vigente FROM viagem vg "
            "  JOIN evento ev ON ev.id = vg.evento_encerramento_id "
            " WHERE vg.status = 'encerrada'").fetchone()[0]
        assert vigente == 1, "a âncora ficou num evento aposentado"

    # e corrigir o que ja fechou NAO abre outra viagem
    with closing(db.conectar()) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM viagem WHERE status = 'aberta'").fetchone()[0] == 1


def test_um_navio_nao_ve_as_viagens_encerradas_do_outro(cliente):
    entrar(cliente, login="navio.pioneer")
    _percorrer(cliente, prefixo="pio")
    cliente.get("/logout")

    entrar(cliente, login="navio.pathfinder")
    html = cliente.get("/navio/encerradas").text
    assert "APN26001" not in html
    assert "Nenhuma viagem encerrada ainda" in html


def test_encerradas_e_so_do_comandante(cliente):
    entrar(cliente, login="vlo")
    resposta = cliente.get("/navio/encerradas", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/painel"


def test_o_painel_ficou_so_com_a_viagem_em_curso(cliente):
    """Juntas, a lista de encerradas empurrava a viagem em curso para fora da
    tela — e é ela que o comandante veio preencher."""
    entrar(cliente)
    _percorrer(cliente)

    painel = cliente.get("/navio").text
    assert "Viagens anteriores" not in painel
    assert painel.count("APT26001") == 0      # a encerrada nao aparece aqui
    assert "APT26002" in painel               # a nova, sim


def test_zero_nunca_aparece_com_sinal_de_menos(cliente):
    """-0,0004 arredondado virava '-0,000' — um menos que nao diz nada."""
    from app.main import formato_mt
    assert formato_mt(-0.0004) == "0,000"
    assert formato_mt(-1.2) == "-1,200"
    assert formato_mt(0) == "0,000"
    assert formato_mt(None) == "\u2014"


def test_o_plural_de_viagem(cliente):
    entrar(cliente)
    assert "Nenhuma viagem encerrada ainda" in cliente.get("/navio/encerradas").text
    _percorrer(cliente)
    html = cliente.get("/navio/encerradas").text
    assert "1</b>\n    viagem encerrada" in html.replace("\r\n", "\n")
    assert "viagems" not in html


# ---------------------------------------------------------------------------
# A marca da Posidonia e o nome do sistema
# ---------------------------------------------------------------------------

def _marca(nome):
    return (pathlib.Path(__file__).resolve().parent.parent
            / "app" / "static" / "marca" / nome)


def test_o_sistema_se_chama_corsair(cliente):
    html = cliente.get("/login").text
    assert "<title>Entrar no Corsair · Posidonia</title>" in html
    entrar(cliente)
    assert "Corsair" in cliente.get("/navio").text


def test_o_tridente_vai_para_a_aba_do_navegador(cliente):
    """Os três tamanhos têm de EXISTIR: um href para arquivo ausente não dá
    erro nenhum — o navegador só mostra o ícone em branco."""
    html = cliente.get("/login").text
    for tamanho in (16, 32, 180):
        assert "/static/marca/icone-{}.png".format(tamanho) in html, tamanho
        assert _marca("icone-{}.png".format(tamanho)).exists(), tamanho
    assert 'rel="apple-touch-icon"' in html


def test_a_barra_lateral_usa_a_marca_branca(cliente):
    """A versão colorida tem o tridente em navy. Sobre a barra, que também é
    navy, ele desapareceria e sobraria a palavra POSIDONIA solta."""
    entrar(cliente)
    html = cliente.get("/navio").text
    assert "posidonia-branca.png" in html
    assert "marca/posidonia.png" not in html       # a colorida fica no login
    assert _marca("posidonia-branca.png").exists()


def test_o_login_usa_a_marca_colorida_sobre_o_fundo_claro(cliente):
    html = cliente.get("/login").text
    assert "marca/posidonia.png" in html
    assert "posidonia-branca.png" not in html


def test_o_casco_diz_em_que_navio_ele_esta_lancando(cliente):
    entrar(cliente)
    html = cliente.get("/navio").text
    assert "marca/navio.png" in html
    # o cadastro guarda o nome oficial em caixa alta
    assert "AMAZON PATHFINDER" in html
    assert _marca("navio.png").exists()


def test_trocar_uma_logo_muda_o_endereco_dos_estaticos(monkeypatch, tmp_path):
    """A marca vive numa SUBPASTA. Varrendo só o primeiro nível, trocar a logo
    não mudaria a impressão digital e a antiga ficaria presa no cache."""
    from app import main

    pasta = tmp_path / "static"
    (pasta / "marca").mkdir(parents=True)
    (pasta / "estilo.css").write_text("body{}", encoding="utf-8")
    (pasta / "marca" / "posidonia.png").write_bytes(b"logo antiga")
    monkeypatch.setattr(main, "ESTATICOS", pasta)
    antes = main.versao_estaticos()

    (pasta / "marca" / "posidonia.png").write_bytes(b"logo nova")
    assert main.versao_estaticos() != antes


def test_todo_item_do_menu_tem_icone(cliente):
    """Um item sem ícone fica torto na barra, e o macro falha em silêncio:
    rota que ele não conhece simplesmente não desenha nada."""
    import re
    entrar(cliente)
    html = cliente.get("/navio").text
    lateral = html[html.index('<nav class="lateral">'):html.index("</nav>")]
    itens = re.findall(r'<a href="[^"]*"[^>]*>(.*?)</a>', lateral, re.S)
    assert itens, "menu vazio"
    for item in itens:
        assert "<svg" in item, item.strip()[:60]


# ---------------------------------------------------------------------------
# Viagens: horas por pernada, e as premissas do admin
# ---------------------------------------------------------------------------

def _admin(cliente):
    with closing(db.conectar()) as conn:
        if contas.buscar(conn, "adm") is None:
            contas.criar_conta(conn, login="adm", senha=SENHA,
                               nome_exibicao="Administrador", perfil="admin")
    entrar(cliente, login="adm")


def _viagem_fechada_do_pathfinder(cliente):
    entrar(cliente)
    _percorrer(cliente)
    cliente.get("/logout")


def test_viagens_poe_uma_coluna_por_viagem_e_a_media_como_referencia(cliente):
    _viagem_fechada_do_pathfinder(cliente)
    entrar(cliente, login="vlo")
    html = cliente.get("/viagens?navio=1").text

    assert "APT26001" in html and "encerrada 08/03" in html
    assert "APT26002" in html and "em curso" in html       # a que abriu em seguida
    assert 'data-pernada="sub_nav_alumar_faz"' in html
    assert "14h35" in html                                 # Sailing Alumar -> Arrival Faz
    assert "51h25" in html                                 # Juruti -> Alumar (total)
    assert "<small>média</small>" in html             # sem premissa: media
    assert "<small>orçado</small>" not in html
    # a carga: 58.000 carregadas em Juruti, 57.500 descarregadas em Alumar
    carregado = html[html.index('data-carga="carregado"'):]
    assert "58.000,000" in carregado[:carregado.index("</div>")]
    rob = html[html.index('data-carga="rob"'):]
    assert "500,000" in rob[:rob.index("</div>")]          # o que FICOU a bordo


def test_a_premissa_do_admin_substitui_a_media(cliente):
    _viagem_fechada_do_pathfinder(cliente)
    _admin(cliente)

    r = cliente.post("/admin/premissas", data={"h_sub_nav_alumar_faz": "47,0"},
                     follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]

    html = cliente.get("/viagens?navio=1").text
    linha = html[html.index('data-pernada="sub_nav_alumar_faz"'):]
    linha = linha[:linha.index("</div>")]
    assert "47h00" in linha and "<small>orçado</small>" in linha
    # 14h35 contra 47h orcadas: bem abaixo, nenhum degrau
    assert "acima-" not in linha
    # as outras pernadas continuam na media
    assert "<small>média</small>" in html


def test_o_degrau_acende_contra_a_premissa(cliente):
    """Orcar 5h para uma navegacao que levou 14h35 e passar de 1,6x: degrau 3."""
    _viagem_fechada_do_pathfinder(cliente)
    _admin(cliente)
    cliente.post("/admin/premissas", data={"h_sub_nav_alumar_faz": "5"})
    html = cliente.get("/viagens?navio=1").text
    linha = html[html.index('data-pernada="sub_nav_alumar_faz"'):]
    linha = linha[:linha.index("</div>")]
    assert "acima-3" in linha


def test_campo_vazio_apaga_a_premissa_e_volta_a_media(cliente):
    _viagem_fechada_do_pathfinder(cliente)
    _admin(cliente)
    cliente.post("/admin/premissas", data={"h_jur_operacao": "24,8"})
    assert "24h48" in cliente.get("/viagens?navio=1").text
    cliente.post("/admin/premissas", data={"h_jur_operacao": ""})
    html = cliente.get("/viagens?navio=1").text
    assert "24h48" not in html
    with closing(db.conectar()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM premissa_pernada").fetchone()[0] == 0


def test_a_tela_de_premissas_mostra_a_media_da_frota_ao_lado_do_campo(cliente):
    _viagem_fechada_do_pathfinder(cliente)
    _admin(cliente)
    html = cliente.get("/admin/premissas").text
    assert 'name="h_des_espera_mare"' in html
    assert "7h40" in html and "1 viagem" in html         # a media, e de quantas


def test_premissas_sao_so_do_admin(cliente):
    entrar(cliente, login="vlo")                         # supervisor
    assert cliente.get("/admin/premissas").status_code == 403
    assert cliente.post("/admin/premissas", data={"h_jur_operacao": "1"}).status_code == 403
    assert "/admin/premissas" not in cliente.get("/viagens").text.split("<main")[0]


def test_viagens_e_da_supervisao_nao_do_comandante(cliente):
    entrar(cliente)
    r = cliente.get("/viagens", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/navio"

    cliente.get("/logout")
    entrar(cliente, login="bi")                          # analytics: ve, nao grava
    assert cliente.get("/viagens").status_code == 200
    assert cliente.post("/admin/premissas", data={}).status_code == 403


def test_pernada_em_curso_diz_que_a_hora_esta_correndo(cliente):
    entrar(cliente)
    cliente.get("/navio")
    alumar = escala_de(cliente, ordem=10)
    cliente.post("/api/marco", json={
        "escala_id": alumar, "tipo": "sailing", "hora_local": "2026-03-01T10:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "cor-1"})
    cliente.get("/logout")

    entrar(cliente, login="vlo")
    html = cliente.get("/viagens?navio=1").text
    linha = html[html.index('data-pernada="sub_nav_alumar_faz"'):]
    linha = linha[:linha.index("</div>")]
    assert "correndo" in linha and 'class="h curso"' in linha
    # a pernada seguinte nem comecou
    seguinte = html[html.index('data-pernada="sub_espera_faz"'):]
    assert "correndo" not in seguinte[:seguinte.index("</div>")]
