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
    assert 'class="situacao"' in html
    assert "Viagem nova" in html                 # nada lancado ainda


def test_uma_acao_obvia_por_vez(cliente):
    """O paredao de quinze formularios abertos foi o que motivou este desenho."""
    entrar(cliente)
    html = cliente.get("/navio").text
    assert html.count('class="lancar destaque"') == 1     # so o proximo em destaque
    assert '<div class="proximo-rot">Próximo lançamento</div>' in html


def test_o_proximo_lancamento_avanca_conforme_a_viagem(cliente):
    """E o que faz a viagem 'se traduzir': o cartao anda sozinho."""
    import re

    entrar(cliente)
    cliente.get("/navio")

    def proximo():
        html = cliente.get("/navio").text
        titulo = re.search(r'<h2 class="proximo-titulo">\s*(\S+)\s*<span class="em">em ([^<]+)<',
                           html)
        return (titulo.group(1), titulo.group(2).strip()) if titulo else None

    assert proximo() == ("Sailing", "Alumar")     # a saida que abre a viagem

    abertura = escala_de(cliente, ordem=10)
    cliente.post("/api/marco", json={
        "escala_id": abertura, "tipo": "sailing", "hora_local": "2026-03-01T10:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "av-1"})

    assert proximo() == ("Arrival", "Fazendinha")  # o cartao andou


def test_trilha_mostra_o_progresso_de_cada_parada(cliente):
    entrar(cliente)
    cliente.get("/navio")
    juruti = escala_de(cliente, ordem=30)
    cliente.post("/api/marco", json={
        "escala_id": juruti, "tipo": "arrival", "hora_local": "2026-03-02T08:00",
        "offset": "-03:00", "nome_responsavel": "Cmt.", "id_cliente": "tr-1"})

    html = cliente.get("/navio").text
    assert 'class="trilha"' in html
    assert "1 de 4" in html                       # por extenso, nao fracao solta
    assert 'class="parada parcial' in html


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
    assert "Icoaraci" in pagina and "fora do padrão" in pagina


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
    assert html.count('<details class="parada') >= 6      # cada porto fecha
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


def test_marcos_ja_lancados_aparecem_na_linha_da_parada(cliente):
    """O ganho do computador: ver a viagem sem abrir parada nenhuma."""
    entrar(cliente)
    cliente.get("/navio")
    juruti = escala_de(cliente, ordem=30)
    for i, (tipo, hora) in enumerate((("arrival", "2026-08-21T04:20"),
                                      ("berth", "2026-08-21T14:00"))):
        cliente.post("/api/marco", json={
            "escala_id": juruti, "tipo": tipo, "hora_local": hora,
            "offset": "-03:00", "nome_responsavel": "Cmt.",
            "id_cliente": "res-{}".format(i)})

    html = cliente.get("/navio").text
    assert 'class="marcos-resumo"' in html
    assert "Arrival 21/08/2026 04:20 · Berth 21/08/2026 14:00" in html
    # e o que AINDA falta, por extenso — o que o "2 de 4" nao diz
    assert "falta Unberth e Sailing" in html


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
    for marca in ('class="tela"', 'class="proximo"', 'class="situacao"',
                  'class="bloco-viagem"'):
        assert marca in html, marca
    assert 'class="coluna-lado"' not in html


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
    assert 'class="tela"' in corpo and 'class="trilha"' in corpo
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
    assert "Sailing 20/08/2026 18:40" in depois


def test_o_fragmento_e_so_do_comandante(cliente):
    entrar(cliente, login="vlo")
    assert cliente.get("/navio/tela").status_code == 403


def test_o_javascript_so_recarrega_quando_nao_ha_alternativa(cliente):
    """Guarda de regressao.

    Um unico `location.reload()` deve restar — o da sessao caida / viagem
    trocada. Se voltar a haver reload no caminho de salvar, esta contagem sobe
    e o teste avisa antes de o comandante sentir a tela travar de novo.
    """
    import re
    js = (pathlib.Path(__file__).resolve().parent.parent
          / "app" / "static" / "escalas.js").read_text(encoding="utf-8")
    # sem os comentarios: o proprio arquivo EXPLICA o reload que foi tirado,
    # e contar a explicacao junto com o codigo esconderia a regressao
    codigo = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", js, flags=re.S))
    assert codigo.count("location.reload()") == 1, codigo.count("location.reload()")
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


def _trilha(cliente):
    """So a lista de paradas.

    O nome do porto tambem aparece no seletor de "acrescentar parada", entao
    procurar no HTML inteiro acharia Icoaraci mesmo depois de ela sair da viagem.
    """
    html = cliente.get("/navio/tela").text
    return html[html.index('<ol class="trilha">'):html.index("</ol>")]


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
    assert "Icoaraci" in _trilha(cliente)

    resposta = _remover(cliente, extra)
    assert resposta.headers["location"] == "/navio"

    # sumiu da trilha do comandante...
    assert "Icoaraci" not in _trilha(cliente)
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
