"""Aplicacao web. FastAPI + Jinja2, sem build step, sem node, sem CDN."""
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analises, auth, bunker, config, contas, db, dominio, frota, importacao, listas, pernadas, sof, viagens

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(RAIZ / "templates"))
templates.env.globals["ROTULO_MOTIVO"] = dominio.ROTULO_MOTIVO
templates.env.globals["ROTULO_MARCO"] = dominio.ROTULO_MARCO


def versao_estaticos() -> str:
    """Impressao digital do CSS e do JS, para o endereco deles mudar a cada deploy.

    O navegador guarda `/static/estilo.css` e continua servindo a copia velha
    depois de um deploy — o Vinicius viu a tela ANTERIOR ao redesenho por causa
    disso. A bordo e pior: ninguem vai ensinar comandante a limpar cache.

    Com `?v=<digest>` no fim, arquivo novo e ENDERECO novo: o navegador nao tem
    o que reaproveitar. Calculado uma vez, no import — dentro do container os
    arquivos nao mudam enquanto o processo vive.
    """
    digest = hashlib.blake2b(digest_size=8)
    # rglob, nao glob: a marca vive em `static/marca/`. Varrendo so o primeiro
    # nivel, trocar a logo nao mudaria o endereco e a antiga ficaria presa no
    # cache — exatamente o problema que esta funcao existe para evitar.
    for arquivo in sorted(ESTATICOS.rglob("*")):
        if arquivo.is_file():
            digest.update(str(arquivo.relative_to(ESTATICOS)).replace("\\", "/").encode("utf-8"))
            digest.update(arquivo.read_bytes())
    return digest.hexdigest()


templates.env.globals["V"] = versao_estaticos()

# Menu por perfil. Declarativo, como o NAV_TREE do Sistema Emissor: menu novo
# nasce de dado, nao de HTML espalhado. O do comandante e curto de proposito —
# o acesso dele e so lancar escala.
MENU = {
    "navio": ((("/navio"), "Painel"), (("/navio/encerradas"), "Encerradas")),
    # Analises e de todos em terra — supervisao, analytics e admin. So o
    # comandante nao ve: o acesso dele e lancar escala.
    "supervisor": ((("/painel"), "Frota"), (("/viagens"), "Viagens"), (("/bunker"), "Bunker"),
                   (("/analises"), "Análises")),
    "analytics": ((("/painel"), "Frota"), (("/viagens"), "Viagens"), (("/bunker"), "Bunker"),
                  (("/analises"), "Análises")),
    # A visao do admin e a da supervisao mais a administracao — e so isso.
    "admin": ((("/painel"), "Frota"), (("/viagens"), "Viagens"), (("/bunker"), "Bunker"),
              (("/analises"), "Análises"),
              (("/admin/contas"), "Contas"), (("/admin/premissas"), "Premissas")),
}
templates.env.globals["MENU"] = MENU
templates.env.globals["ROTULO_CONDICAO"] = dominio.ROTULO_CONDICAO


def classe_porto(codigo: str) -> str:
    """'BARRA_NORTE' -> 'porto-barra-norte': a classe com a cor do porto."""
    return "porto-" + (codigo or "").lower().replace("_", "-")


templates.env.filters["classe_porto"] = classe_porto
templates.env.globals["LISTAS"] = listas


def formato_br(iso):
    """'2026-08-21T04:20' -> '21/08/2026 04:20'.

    So EXIBICAO. O banco continua em ISO 8601, que e o que ordena certo e o que
    as contas de duracao usam — trocar o armazenamento por causa da leitura
    quebraria as duas coisas.
    """
    if not iso:
        return ""
    partes = str(iso)[:10].split("-")
    if len(partes) != 3:
        return str(iso)
    data = "{}/{}/{}".format(partes[2], partes[1], partes[0])
    hora = str(iso)[11:16]
    return (data + " " + hora).strip()


def formato_mt(valor, casas=3):
    """1234.5 -> '1.234,500'. Tres casas, no padrao brasileiro."""
    if valor is None or valor == "":
        return "\u2014"
    try:
        numero = float(valor)
        # Arredondar -0.0004 dava "-0,000": sinal de menos num zero e ruido, e
        # some justamente a informacao que o sinal deveria carregar.
        if round(numero, casas) == 0:
            numero = 0.0
        texto = "{:,.{}f}".format(numero, casas)
    except (TypeError, ValueError):
        return str(valor)
    # de 1,234.500 para 1.234,500
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


templates.env.filters["br"] = formato_br
templates.env.filters["mt"] = formato_mt


def _garantir_admin() -> None:
    """Cria (ou realinha) a conta de administrador a partir do ambiente.

    Em producao no plano free do Render NAO existe terminal: sem isto o sistema
    sobe e ninguem consegue entrar. A regra e simples e previsivel — a senha do
    admin e o que SHIPOPS_ADMIN_SENHA disser. E tambem o caminho de recuperacao
    se a senha se perder: troca-se a variavel e redeploya.
    """
    if not config.ADMIN_LOGIN or not config.ADMIN_SENHA:
        return
    with closing(db.conectar()) as conn:
        if contas.buscar(conn, config.ADMIN_LOGIN) is None:
            contas.criar_conta(
                conn, login=config.ADMIN_LOGIN, senha=config.ADMIN_SENHA,
                nome_exibicao="Administrador", perfil="admin")
        elif contas.autenticar(conn, config.ADMIN_LOGIN, config.ADMIN_SENHA) is None:
            contas.trocar_senha(conn, config.ADMIN_LOGIN, config.ADMIN_SENHA)


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    # Falha aqui e falha cedo: melhor nao subir do que subir sem segredo.
    config.secret_key()
    config.checar_persistencia()
    db.inicializar()
    _garantir_admin()
    with closing(db.conectar()) as conn:
        viagens.normalizar_viagens_vazias(conn)
    yield


app = FastAPI(title="Corsair — Posidonia", lifespan=ciclo_de_vida,
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

PUBLICOS = ("/login", "/logout", "/api/health", "/static", "/favicon")
METODOS_LEITURA = ("GET", "HEAD", "OPTIONS")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'"
)


# ---------------------------------------------------------------------------
# Middlewares
# ---------------------------------------------------------------------------

@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, resposta_seguinte):
    resposta = await resposta_seguinte(request)
    resposta.headers["Content-Security-Policy"] = CSP
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "same-origin"
    resposta.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    if request.url.scheme == "https":
        resposta.headers["Strict-Transport-Security"] = "max-age=31536000"
    if "text/html" in resposta.headers.get("content-type", ""):
        resposta.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static"):
        # O endereco carrega a impressao digital do arquivo (ver versao_estaticos):
        # conteudo novo e endereco novo. Entao o velho pode ser guardado para
        # sempre — e a bordo isso vira economia de banda, nao risco de tela velha.
        resposta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resposta


@app.middleware("http")
async def exigir_login(request: Request, resposta_seguinte):
    """Guarda global: rota nova nasce protegida, sem precisar lembrar de nada."""
    caminho = request.url.path
    if caminho.startswith(PUBLICOS):
        return await resposta_seguinte(request)

    login = auth.verificar_sessao(request.cookies.get(auth.COOKIE_SESSAO, ""))
    conta = None
    if login:
        with closing(db.conectar()) as conn:
            conta = contas.buscar(conn, login)

    if conta is None:
        destino = caminho + (("?" + request.url.query) if request.url.query else "")
        resposta = RedirectResponse("/login?next=" + quote(destino, safe=""), 303)
        resposta.delete_cookie(auth.COOKIE_SESSAO, path="/")
        return resposta

    if conta["perfil"] in contas.PERFIS_SOMENTE_LEITURA and request.method not in METODOS_LEITURA:
        return HTMLResponse("<h3>Acesso somente leitura.</h3>", status_code=403)

    request.state.conta = conta
    return await resposta_seguinte(request)


# ---------------------------------------------------------------------------
# Saude e sessao
# ---------------------------------------------------------------------------

@app.get("/api/health")
def saude():
    """Publico e sem versao: o Render depende dele e ele nao anuncia nada."""
    try:
        with closing(db.conectar()) as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "erro"}, status_code=503)


def _destino_seguro(bruto: str | None) -> str:
    """Bloqueia open-redirect: so caminho interno."""
    if bruto and bruto.startswith("/") and not bruto.startswith("//"):
        return bruto
    return "/"


@app.get("/login", response_class=HTMLResponse)
def tela_login(request: Request, next: str = "/", erro: str = ""):
    return templates.TemplateResponse(
        request, "login.html", {"next": _destino_seguro(next), "erro": erro})


@app.post("/login")
def entrar(request: Request, login: str = Form(""), senha: str = Form(""),
           next: str = Form("/")):
    destino = _destino_seguro(next)
    with closing(db.conectar()) as conn:
        conta = contas.autenticar(conn, login, senha)
        if conta is None:
            contas.registrar_acesso(conn, login, "login_negado", request.client.host
                                    if request.client else None)
            return RedirectResponse(
                "/login?next={}&erro={}".format(
                    quote(destino, safe=""), quote("Login ou senha inválidos.")), 303)
        contas.registrar_acesso(conn, conta["login"], "login",
                                request.client.host if request.client else None)

    resposta = RedirectResponse(destino, 303)
    resposta.set_cookie(
        auth.COOKIE_SESSAO, auth.assinar_sessao(conta["login"]),
        max_age=auth.DURACAO_SESSAO, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), path="/")
    return resposta


@app.get("/logout")
def sair():
    resposta = RedirectResponse("/login", 303)
    resposta.delete_cookie(auth.COOKIE_SESSAO, path="/")
    return resposta


@app.get("/")
def raiz(request: Request):
    perfil = request.state.conta["perfil"]
    return RedirectResponse("/navio" if perfil == "navio" else "/painel", 303)


# ---------------------------------------------------------------------------
# Telas do comandante
# ---------------------------------------------------------------------------

def _viagem_do_navio(conn, navio_id: int):
    return conn.execute(
        "SELECT id, numero, status FROM viagem "
        " WHERE navio_id = ? AND status = 'aberta'", (navio_id,)).fetchone()


def _duracao(minutos: int | None) -> str:
    """125 -> '2h05'. Em horas e minutos, que e como se le uma escala.

    O banco guarda o instante em UTC e as views calculam em horas decimais, que
    e o que laytime usa. Aqui e leitura de tela: '32h10' diz mais a quem esta a
    bordo do que '32,17'.
    """
    if minutos is None or minutos < 0:
        return ""
    return "{}h{:02d}".format(minutos // 60, minutos % 60)


def _duracao_longa(minutos: int | None) -> str:
    """11.227 -> '7d 19h'. Uma viagem leva dias; '187h07' nao se le."""
    if minutos is None or minutos < 0:
        return ""
    dias, resto = divmod(minutos // 60, 24)
    return "{}d {:02d}h".format(dias, resto) if dias else "{}h{:02d}".format(
        resto, minutos % 60)


def _instante_utc(iso: str) -> datetime:
    """Le um instante ISO e devolve-o em UTC, SEM fuso — comparavel a _agora_utc().

    O banco guarda `hora_utc` com o fuso explicito (+00:00); `_agora_utc()` e
    nu. Subtrair um do outro levanta TypeError, que `_entre` engolia — e a
    frota mostrava "—" no "Ha", e a pernada em curso de Viagens saia
    "· correndo" sem as horas. Os testes nao pegaram porque "correndo" estava
    la; so as horas e que nao.
    """
    valor = datetime.fromisoformat(iso)
    if valor.tzinfo is not None:
        valor = valor.astimezone(timezone.utc).replace(tzinfo=None)
    return valor


def _entre(inicio: str | None, fim: str | None) -> int | None:
    """Minutos entre dois instantes UTC. None se algum nao existe."""
    try:
        return int((_instante_utc(fim) - _instante_utc(inicio)).total_seconds() // 60)
    except (TypeError, ValueError):
        return None


def _janela(marcos) -> dict:
    """Do primeiro ao ultimo lancamento da escala.

    A conta e sobre `hora_utc`, nunca sobre o horario local: dois portos com
    offsets diferentes dariam duracao errada — e um dia darao, quando a frota
    sair da costa norte.
    """
    momentos = []
    for marco in marcos:
        try:
            momentos.append((datetime.fromisoformat(marco["hora_utc"]), marco))
        except (TypeError, ValueError):
            continue
    if not momentos:
        return {"inicio": None, "fim": None, "duracao": ""}
    momentos.sort(key=lambda par: par[0])
    minutos = int((momentos[-1][0] - momentos[0][0]).total_seconds() // 60)
    return {
        "inicio": momentos[0][1]["hora_local"],
        # Um marco so nao delimita janela nenhuma: inicio e fim seriam o mesmo
        # instante, e mostrar "0h00" sugeriria uma escala instantanea.
        "fim": momentos[-1][1]["hora_local"] if len(momentos) > 1 else None,
        "duracao": _duracao(minutos) if len(momentos) > 1 else "",
    }


def _escalas_com_marcos(conn, viagem_id: int) -> list[dict]:
    """Cada escala com a lista de marcos que ELA pede, na ordem cronologica."""
    escalas = conn.execute(
        "SELECT e.id, e.ordem, e.codigo_porto, e.tipo_escala, e.sentido, e.motivo, "
        "       e.origem, e.condicao, p.nome AS porto_nome, p.offset_padrao "
        "  FROM escala e JOIN porto p ON p.codigo = e.codigo_porto "
        " WHERE e.viagem_id = ? AND e.status <> 'cancelada' ORDER BY e.ordem",
        (viagem_id,)).fetchall()

    saida = []
    for escala in escalas:
        exigidos = [r[0] for r in conn.execute(
            "SELECT tipo_evento FROM marco_exigido WHERE tipo_escala = ? ORDER BY ordem",
            (escala["tipo_escala"],))]
        lancados = {r["tipo"]: r for r in conn.execute(
            "SELECT tipo, hora_local, hora_utc, offset_utc, nome_responsavel, versao, "
            "       observacao, rob_vlsfo, rob_mgo, fw, lixo, comentarios, motivo_correcao "
            "  FROM evento_vigente WHERE escala_id = ?", (escala["id"],))}
        bunker = conn.execute(
            "SELECT vlsfo, mgo, nome_responsavel FROM abastecimento WHERE escala_id = ?",
            (escala["id"],)).fetchone()
        carga = conn.execute(
            "SELECT carregado, descarregado, nome_responsavel "
            "  FROM movimento_carga WHERE escala_id = ?", (escala["id"],)).fetchone()
        saida.append({
            "escala": escala,
            "marcos": [{
                "tipo": t,
                "rotulo": dominio.ROTULO_MARCO[t],
                "curto": dominio.ROTULO_CURTO[t],
                "lancado": lancados.get(t),
                # O Unberth de Alumar e o ultimo lancamento da viagem: quando
                # ele e salvo, a viagem fecha, outra abre e a pagina mudou toda.
                "encerra": (t == "unberth"
                            and escala["tipo_escala"] == "encerramento"),
            } for t in exigidos],
            "completa": all(t in lancados for t in exigidos),
            # So escala de bunker pede quantidade abastecida.
            "e_bunker": escala["motivo"] == "bunker",
            "bunker": bunker,
            # So quem carrega ou descarrega movimenta carga. A direcao vem da
            # condicao, nunca de uma escolha do comandante.
            "movimenta_carga": escala["condicao"] in ("loading", "discharging"),
            "carrega": escala["condicao"] == "loading",
            "carga": carga,
            # o Statement of Facts da parada: marcos alem dos quatro, e os numeros
            "sof": sof.marcos_da_escala(conn, escala["id"]),
            "sof_dados": sof.dados_da_escala(conn, escala["id"]),
            "sof_marcos_possiveis": sof.marcos_para(escala["tipo_escala"]),
            **_janela(lancados.values()),
        })
    return saida


def _proximo_lancamento(blocos):
    """A primeira coisa que falta, na ordem da viagem.

    E o que permite a tela ter UMA acao obvia em vez de quinze formularios
    abertos: num celular, quinze formularios sao um paredao.
    """
    for bloco in blocos:
        for marco in bloco["marcos"]:
            if not marco["lancado"]:
                return {"escala": bloco["escala"], "marco": marco,
                        "bunker": False, "carga": False}
        if bloco["e_bunker"] and bloco["bunker"] is None:
            return {"escala": bloco["escala"], "marco": None,
                    "bunker": True, "carga": False}
        if bloco["movimenta_carga"] and bloco["carga"] is None:
            return {"escala": bloco["escala"], "marco": None,
                    "bunker": False, "carga": True,
                    "carrega": bloco["carrega"]}
    return None


def _lista_pt(nomes) -> str:
    """['Unberth', 'Sailing'] -> 'Unberth e Sailing'."""
    nomes = list(nomes)
    if len(nomes) <= 1:
        return "".join(nomes)
    return "{} e {}".format(", ".join(nomes[:-1]), nomes[-1])


def _resumo_marcos(marcos) -> str:
    """'Arrival 02/03/2026 22:30 · Berth 03/03/2026 06:10' — o que ja foi lancado.

    Pelo nome inteiro do marco e com a data inteira. Ja houve uma versao com
    inicial e data curta (A · B · U · S, 02/03) e o Vinicius recusou: vira
    codigo a decifrar num sistema que tem as palavras certas.
    """
    feitos = [
        "{} {}".format(marco["curto"], formato_br(marco["lancado"]["hora_local"])).strip()
        for marco in marcos if marco["lancado"]
    ]
    return " · ".join(feitos)


def _falta_marcos(marcos) -> str:
    """'Unberth e Sailing' — o que a parada ainda espera.

    E o que a contagem '2 de 4' nao diz: QUAL marco falta. Sem isso o
    comandante precisa abrir a parada para descobrir.
    """
    return _lista_pt(marco["curto"] for marco in marcos if not marco["lancado"])


def _situacao(blocos):
    """Onde o navio esta e o que estava fazendo, pelo ultimo marco lancado."""
    ultimo = None
    for bloco in blocos:
        for marco in bloco["marcos"]:
            if marco["lancado"]:
                ultimo = {"escala": bloco["escala"], "marco": marco}
    return ultimo


def _contexto_navio(conta) -> dict:
    """Tudo o que a tela do comandante precisa.

    Serve as DUAS rotas: a pagina inteira e o fragmento de `GET /navio/tela`.
    Uma funcao so porque o fragmento tem que mostrar exatamente o mesmo que a
    pagina mostraria — se as duas montassem contexto por conta propria, um dia
    divergiriam e o comandante veria uma tela que nao existe.
    """
    with closing(db.conectar()) as conn:
        # Sem viagem aberta o comandante ficaria sem onde lancar.
        if _viagem_do_navio(conn, conta["navio_id"]) is None:
            viagens.abrir_viagem(conn, conta["navio_id"], por=conta["login"])

        linhas = conn.execute(
            "SELECT vg.id, vg.numero, vg.status, vg.meta_observacoes, ev.hora_local AS abertura "
            "  FROM viagem vg "
            "  LEFT JOIN evento ev ON ev.id = vg.evento_abertura_id "
            " WHERE vg.navio_id = ? "
            " ORDER BY (vg.status = 'aberta') DESC, vg.id DESC LIMIT 6",
            (conta["navio_id"],)).fetchall()

        lista = []
        for viagem in linhas:
            faltantes = conn.execute(
                "SELECT COUNT(*) FROM escalas_incompletas WHERE viagem_id = ?",
                (viagem["id"],)).fetchone()[0]
            blocos = _escalas_com_marcos(conn, viagem["id"])
            for bloco in blocos:
                lancados = sum(1 for m in bloco["marcos"] if m["lancado"])
                bloco["lancados"] = lancados
                bloco["total"] = len(bloco["marcos"])
                bloco["estado"] = ("pronta" if bloco["completa"]
                                   else "parcial" if lancados else "vazia")
                bloco["resumo"] = _resumo_marcos(bloco["marcos"])
                bloco["falta"] = _falta_marcos(bloco["marcos"])
            lista.append({
                "viagem": viagem,
                "aberta": viagem["status"] == "aberta",
                "faltantes": faltantes,
                "blocos": blocos,
                # O Sailing de Alumar que abre a viagem — o comeco da historia,
                # que antes ficava invisivel para quem estava preenchendo.
                "abertura": viagem["abertura"],
                "proximo": _proximo_lancamento(blocos),
                "situacao": _situacao(blocos),
            })

        portos = conn.execute(
            "SELECT codigo, nome FROM porto WHERE ativo = 1 ORDER BY nome").fetchall()

    corrente = next((v for v in lista if v["aberta"]), None)
    lancados = sum(len([m for m in b["marcos"] if m["lancado"]])
                   for b in (corrente["blocos"] if corrente else []))
    total = sum(len(b["marcos"]) for b in (corrente["blocos"] if corrente else []))

    # A sugestao para os campos de ROB e FW: o que o ultimo marco lancado
    # trouxe. O comandante corrige o que mudou, em vez de digitar tudo de novo.
    ultimo = (corrente["situacao"] or {}).get("marco") if corrente else None
    lancado = ultimo["lancado"] if ultimo else None
    sugestao = {chave: lancado[chave] for chave in ("rob_vlsfo", "rob_mgo", "fw")} if lancado else {}

    # A parada do SOF: a que esta sendo operada agora — a proxima etapa, ou a
    # ultima com marco quando a viagem ja acabou.
    parada_sof = None
    if corrente:
        if corrente["proximo"]:
            parada_sof = next((b for b in corrente["blocos"]
                               if b["escala"]["id"] == corrente["proximo"]["escala"]["id"]), None)
        if parada_sof is None:
            com_marco = [b for b in corrente["blocos"] if any(m["lancado"] for m in b["marcos"])]
            parada_sof = com_marco[-1] if com_marco else None

    return {"conta": conta, "lista": lista, "portos": portos,
            "corrente": corrente, "lancados": lancados, "total": total,
            "sugestao": {k: v for k, v in sugestao.items() if v is not None},
            "parada_sof": parada_sof, "campos_sof": sof.campos_dados()}


@app.get("/navio", response_class=HTMLResponse)
def navio_inicio(request: Request):
    conta = request.state.conta
    if conta["perfil"] != "navio":
        return RedirectResponse("/painel", 303)
    return templates.TemplateResponse(
        request, "navio_inicio.html", _contexto_navio(conta))


@app.get("/navio/tela", response_class=HTMLResponse)
def navio_tela(request: Request):
    """So a tela da viagem, sem a pagina em volta.

    E o que substituiu o `location.reload()` depois de cada lancamento. O
    reload deixava a tela em branco enquanto o Render acordava — 20 a 50
    segundos parecendo travamento, logo depois de o comandante clicar Salvar.
    Aqui so o miolo volta, e a pagina nunca pisca.
    """
    conta = request.state.conta
    if conta["perfil"] != "navio":
        return HTMLResponse("", status_code=403)
    contexto = _contexto_navio(conta)
    if contexto["corrente"] is None:
        # Sem viagem aberta a tela inteira muda de forma; o JavaScript recarrega.
        return HTMLResponse("", status_code=409)
    return templates.TemplateResponse(request, "_tela_viagem.html", contexto)


@app.post("/api/marco")
async def api_marco(request: Request):
    corpo = await request.json()
    conta = request.state.conta

    with closing(db.conectar()) as conn:
        escala = conn.execute(
            "SELECT e.id, vg.navio_id FROM escala e "
            "  JOIN viagem vg ON vg.id = e.viagem_id WHERE e.id = ?",
            (corpo.get("escala_id"),)).fetchone()
        if escala is None:
            return JSONResponse({"ok": False, "erros": ["Escala não encontrada."]}, 404)
        if conta["perfil"] == "navio" and escala["navio_id"] != conta["navio_id"]:
            return JSONResponse({"ok": False, "erros": ["Escala de outro navio."]}, 403)

        evento_id, erros = viagens.lancar_marco(
            conn, escala["id"],
            tipo=corpo.get("tipo"),
            hora_local=corpo.get("hora_local"),
            offset=corpo.get("offset"),
            nome_responsavel=corpo.get("nome_responsavel", ""),
            registrado_por=conta["login"],
            id_cliente=corpo.get("id_cliente"),
            observacao=(corpo.get("observacao") or "").strip() or None,
            motivo_correcao=(corpo.get("motivo_correcao") or "").strip() or None,
            rob_vlsfo=corpo.get("rob_vlsfo"),
            rob_mgo=corpo.get("rob_mgo"),
            fw=corpo.get("fw"),
            lixo=corpo.get("lixo"),
            comentarios=corpo.get("comentarios"),
        )

    if erros:
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)

    with closing(db.conectar()) as conn:
        ainda_aberta = conn.execute(
            "SELECT 1 FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE e.id = ? AND vg.status = 'aberta'", (escala["id"],)).fetchone()
    return {"ok": True, "evento_id": evento_id, "viagem_mudou": ainda_aberta is None}


@app.post("/api/abastecimento")
async def api_abastecimento(request: Request):
    """Quanto entrou de combustivel nesta escala. Mesmo contrato do marco:
    sempre JSON, 422 no dado invalido para a fila nao repetir."""
    corpo = await request.json()
    conta = request.state.conta

    with closing(db.conectar()) as conn:
        escala = conn.execute(
            "SELECT e.id, vg.navio_id FROM escala e "
            "  JOIN viagem vg ON vg.id = e.viagem_id WHERE e.id = ?",
            (corpo.get("escala_id"),)).fetchone()
        if escala is None:
            return JSONResponse({"ok": False, "erros": ["Escala não encontrada."]}, 404)
        if conta["perfil"] == "navio" and escala["navio_id"] != conta["navio_id"]:
            return JSONResponse({"ok": False, "erros": ["Escala de outro navio."]}, 403)

        gravou, erros = viagens.registrar_abastecimento(
            conn, escala["id"],
            vlsfo=corpo.get("vlsfo"),
            mgo=corpo.get("mgo"),
            nome_responsavel=corpo.get("nome_responsavel", ""),
            registrado_por=conta["login"],
            observacao=(corpo.get("observacao") or "").strip() or None)

    if erros:
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)
    return {"ok": True}


@app.post("/api/carga")
async def api_carga(request: Request):
    """Quanto de carga entrou ou saiu nesta escala, em MT.

    Mesmo contrato do marco: sempre JSON, 422 no dado invalido para a fila nao
    ficar repetindo. A direcao (carrega ou descarrega) vem da condicao da
    escala, nao do corpo da requisicao.
    """
    corpo = await request.json()
    conta = request.state.conta

    with closing(db.conectar()) as conn:
        escala = conn.execute(
            "SELECT e.id, vg.navio_id FROM escala e "
            "  JOIN viagem vg ON vg.id = e.viagem_id WHERE e.id = ?",
            (corpo.get("escala_id"),)).fetchone()
        if escala is None:
            return JSONResponse({"ok": False, "erros": ["Escala não encontrada."]}, 404)
        if conta["perfil"] == "navio" and escala["navio_id"] != conta["navio_id"]:
            return JSONResponse({"ok": False, "erros": ["Escala de outro navio."]}, 403)

        gravou, erros = viagens.registrar_movimento_carga(
            conn, escala["id"],
            quantidade=corpo.get("quantidade"),
            nome_responsavel=corpo.get("nome_responsavel", ""),
            registrado_por=conta["login"],
            observacao=(corpo.get("observacao") or "").strip() or None)

    if erros:
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)
    return {"ok": True}


# Quantas viagens encerradas a tela mostra por padrao. Com o historico
# importado sao dezenas por navio, e cada uma vem com as paradas e os marcos:
# a pagina inteira passava de 700 KB num celular a bordo. O resto fica atras
# de "ver todas".
LIMITE_ENCERRADAS = 5


def _encerradas(conta, busca: str = "", ano: str = "", todas: bool = False) -> dict:
    """As viagens ja fechadas deste navio, para consulta e correcao.

    A carga e a soma do DESCARREGADO: e ela que diz o que a viagem entregou.
    O carregado ja aparece na parada de Juruti, dentro da propria viagem.

    O filtro (ano, busca por codigo ou porto) e aplicado ANTES de carregar as
    paradas, e so as viagens que vao aparecer sao montadas por inteiro.
    """
    with closing(db.conectar()) as conn:
        linhas = conn.execute(
            "SELECT vg.id, vg.numero, "
            "       ab.hora_local AS saida, ab.hora_utc AS saida_utc, "
            "       en.hora_local AS chegada, en.hora_utc AS chegada_utc, "
            "       (SELECT GROUP_CONCAT(p.nome, ' ') FROM escala e "
            "          JOIN porto p ON p.codigo = e.codigo_porto "
            "         WHERE e.viagem_id = vg.id AND e.status <> 'cancelada') AS portos, "
            "       (SELECT COUNT(*) FROM escala e "
            "         WHERE e.viagem_id = vg.id AND e.status <> 'cancelada') AS paradas, "
            "       (SELECT COUNT(*) FROM escala e "
            "         WHERE e.viagem_id = vg.id AND e.status <> 'cancelada' "
            "           AND e.origem = 'extra') AS adicionais, "
            "       (SELECT COUNT(*) FROM escalas_incompletas i "
            "         WHERE i.viagem_id = vg.id) AS faltantes, "
            "       (SELECT ROUND(SUM(mc.descarregado), 3) FROM movimento_carga mc "
            "          JOIN escala e ON e.id = mc.escala_id "
            "         WHERE e.viagem_id = vg.id) AS descarregado "
            "  FROM viagem vg "
            "  LEFT JOIN evento ab ON ab.id = vg.evento_abertura_id "
            "  LEFT JOIN evento en ON en.id = vg.evento_encerramento_id "
            " WHERE vg.navio_id = ? AND vg.status = 'encerrada' "
            " ORDER BY vg.id DESC", (conta["navio_id"],)).fetchall()

        anos = sorted({(v["saida"] or "")[:4] for v in linhas if v["saida"]}, reverse=True)
        procurado = (busca or "").strip().lower()
        filtradas = [
            v for v in linhas
            if (not ano or (v["saida"] or "")[:4] == ano)
            and (not procurado
                 or procurado in (v["numero"] or "").lower()
                 or procurado in (v["portos"] or "").lower())
        ]
        total = len(filtradas)
        if not todas:
            filtradas = filtradas[:LIMITE_ENCERRADAS]

        viagens_lista = []
        for viagem in filtradas:
            ano_da = (viagem["saida"] or "")[:4]
            blocos = _escalas_com_marcos(conn, viagem["id"])
            for bloco in blocos:
                lancados = sum(1 for m in bloco["marcos"] if m["lancado"])
                bloco["lancados"] = lancados
                bloco["total"] = len(bloco["marcos"])
                bloco["estado"] = ("pronta" if bloco["completa"]
                                   else "parcial" if lancados else "vazia")

            # Somas da viagem: o que a escala ja calcula, acumulado.
            espera = atracado = 0
            for bloco in blocos:
                marcos = {m["tipo"]: m["lancado"] for m in bloco["marcos"]
                          if m["lancado"]}
                for de, para, alvo in (("arrival", "berth", "espera"),
                                       ("berth", "unberth", "atracado")):
                    if de in marcos and para in marcos:
                        minutos = _entre(marcos[de]["hora_utc"], marcos[para]["hora_utc"])
                        if minutos and minutos > 0:
                            if alvo == "espera":
                                espera += minutos
                            else:
                                atracado += minutos

            consumo = conn.execute(
                "SELECT ROUND(SUM(consumo_vlsfo), 3), ROUND(SUM(consumo_mgo), 3) "
                "  FROM consumo_combustivel "
                " WHERE viagem = ? AND consumo_vlsfo IS NOT NULL",
                (viagem["numero"],)).fetchone()

            viagens_lista.append({
                "viagem": viagem,
                "blocos": blocos,
                "ano": ano_da,
                "duracao": _duracao_longa(_entre(viagem["saida_utc"],
                                                 viagem["chegada_utc"])),
                "espera": _duracao(espera) if espera else "",
                "atracado": _duracao(atracado) if atracado else "",
                "consumo_vlsfo": consumo[0] if consumo else None,
                "consumo_mgo": consumo[1] if consumo else None,
            })

    return {
        "encerradas": viagens_lista,
        "total": total,
        "todas": todas,
        "limite": LIMITE_ENCERRADAS,
        "anos": anos,
        "ano": ano,
        "busca": busca or "",
        "com_pendencia": sum(1 for v in viagens_lista if v["viagem"]["faltantes"]),
    }


@app.get("/navio/encerradas", response_class=HTMLResponse)
def navio_encerradas(request: Request, busca: str = "", ano: str = "", todas: str = ""):
    """Consulta e correcao do que ja passou.

    Tela separada porque o painel responde "o que lanco agora" e esta responde
    "o que aconteceu" — perguntas diferentes, em momentos diferentes do dia.
    """
    conta = request.state.conta
    if conta["perfil"] != "navio":
        return RedirectResponse("/painel", 303)
    contexto = {"conta": conta}
    contexto.update(_encerradas(conta, busca=busca, ano=ano, todas=todas == "1"))
    return templates.TemplateResponse(request, "encerradas.html", contexto)


@app.post("/navio/escala-extra")
def navio_escala_extra(
    request: Request,
    codigo_porto: str = Form(...),
    motivo: str = Form(...),
    apos_ordem: int = Form(...),
    tipo_escala: str = Form("fundeio"),
):
    conta = request.state.conta
    with closing(db.conectar()) as conn:
        viagem = _viagem_do_navio(conn, conta["navio_id"])
        if viagem is None:
            return RedirectResponse("/navio", 303)
        _, erros = viagens.adicionar_escala_extra(
            conn, viagem["id"], codigo_porto=codigo_porto, tipo_escala=tipo_escala,
            motivo=motivo, apos_ordem=apos_ordem, por=conta["login"])
    destino = "/navio"
    if erros:
        destino += "?erro=" + quote(" | ".join(erros))
    return RedirectResponse(destino, 303)


@app.post("/navio/escala-extra/remover")
def navio_escala_extra_remover(request: Request, escala_id: int = Form(...)):
    """Desfaz uma parada acrescentada por engano.

    O `escala_id` vem do formulario, entao o vinculo com o navio da sessao e
    conferido AQUI: sem isso um comandante removeria parada de outro navio so
    trocando o numero.
    """
    conta = request.state.conta
    with closing(db.conectar()) as conn:
        dono = conn.execute(
            "SELECT 1 FROM escala e JOIN viagem vg ON vg.id = e.viagem_id "
            " WHERE e.id = ? AND vg.navio_id = ?",
            (escala_id, conta["navio_id"])).fetchone()
        erros = (["Parada não encontrada nesta embarcação."] if dono is None
                 else viagens.remover_escala_extra(conn, escala_id)[1])
    destino = "/navio"
    if erros:
        destino += "?erro=" + quote(" | ".join(erros))
    return RedirectResponse(destino, 303)


# ---------------------------------------------------------------------------
# Painel da equipe em terra (esqueleto — Fase 3)
# ---------------------------------------------------------------------------

MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro")


@app.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    """A frota agora: em que pernada cada navio esta, e ha quanto tempo.

    A posicao vem do ultimo marco lancado (app/frota.py) — ninguem digita
    "posicao". "Em falta" sao marcos que ficaram PARA TRAS, nao os que ainda
    vao acontecer: e a diferenca entre cobrar e esperar.
    """
    conta = request.state.conta
    if conta["perfil"] == "navio":
        return RedirectResponse("/navio", 303)
    agora_utc, agora_local = _agora_utc(), db.agora()

    with closing(db.conectar()) as conn:
        navios = conn.execute(
            "SELECT id, nome_oficial FROM navio WHERE ativo = 1 ORDER BY id").fetchall()
        lista, total_conferir, total_falta, navios_falta, em_viagem = [], 0, 0, [], 0
        for n in navios:
            viagem = conn.execute(
                "SELECT id, numero FROM viagem WHERE navio_id = ? AND status = 'aberta' "
                " ORDER BY id DESC LIMIT 1", (n["id"],)).fetchone()
            blocos = _escalas_com_marcos(conn, viagem["id"]) if viagem else []
            situacao = (frota.pernada_atual(blocos) if blocos else
                        {"titulo": "Sem viagem aberta", "detalhe": "", "condicao": None,
                         "ultimo": None})
            pulados = frota.marcos_pulados(blocos) if blocos else []
            conferir = conn.execute(
                "SELECT COUNT(*) FROM escalas_a_conferir WHERE viagem_id = ?",
                (viagem["id"],)).fetchone()[0] if viagem else 0
            ha = ""
            if situacao["ultimo"]:
                em_viagem += 1
                ha = _duracao_longa(_entre(situacao["ultimo"]["hora_utc"], agora_utc))
            if pulados:
                total_falta += len(pulados)
                navios_falta.append(n["nome_oficial"].replace("AMAZON ", "").title())
            total_conferir += conferir
            lista.append({"id": n["id"], "nome": n["nome_oficial"],
                          "numero": viagem["numero"] if viagem else None,
                          "situacao": situacao, "ha": ha, "pulados": pulados,
                          "a_conferir": conferir})

        encerradas_mes = conn.execute(
            "SELECT COUNT(*) FROM viagem vg "
            "  JOIN evento en ON en.id = vg.evento_encerramento_id "
            " WHERE vg.status = 'encerrada' AND substr(en.hora_local, 1, 7) = ?",
            (agora_local[:7],)).fetchone()[0]

    return templates.TemplateResponse(request, "painel.html", {
        "conta": conta, "frota": lista, "em_viagem": em_viagem,
        "a_conferir": total_conferir, "em_falta": total_falta,
        "navios_em_falta": navios_falta, "encerradas_mes": encerradas_mes,
        "mes_nome": MESES[int(agora_local[5:7]) - 1], "agora_local": agora_local,
    })


# ---------------------------------------------------------------------------
# Viagens: horas por pernada
#
# A planilha "analise viagens" das supervisoras, calculada. Colunas sao
# viagens, linhas sao pernadas, um navio por vez. A ultima coluna e a
# REFERENCIA: a premissa cadastrada pelo admin ou, na falta dela, a media
# das encerradas mostradas.
# ---------------------------------------------------------------------------

def _agora_utc() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes")


def _colunas_de_viagens(conn, navio_id: int) -> list[dict]:
    """A viagem em curso (se houver) e as ultimas cinco encerradas."""
    # Duas consultas simples, nao um UNION com subconsulta ordenada: o Turso
    # remoto tem parser proprio e ja recusou SQL que o SQLite local aceita.
    BASE = (
        "SELECT vg.id, vg.numero, vg.status, "
        "       ab.hora_local AS inicio, en.hora_local AS termino "
        "  FROM viagem vg "
        "  LEFT JOIN evento ab ON ab.id = vg.evento_abertura_id "
        "  LEFT JOIN evento en ON en.id = vg.evento_encerramento_id "
        " WHERE vg.navio_id = ? AND vg.status = ? ORDER BY vg.id DESC LIMIT ?")
    linhas = (conn.execute(BASE, (navio_id, "aberta", 1)).fetchall()
              + conn.execute(BASE, (navio_id, "encerrada", 5)).fetchall())
    colunas = []
    for v in linhas:
        valores = pernadas.calcular(conn, v["id"])
        aberta = v["status"] == "aberta"
        # A carga: o que subiu em Juruti e o que FICOU a bordo depois da descarga
        # em Alumar. Nunca se descarrega exatamente o que se carregou; a sobra
        # atravessa para a viagem seguinte, e a view carga_bordo ja a acumula.
        carregado = conn.execute(
            "SELECT ROUND(SUM(mc.carregado), 3) FROM movimento_carga mc "
            "  JOIN escala e ON e.id = mc.escala_id WHERE e.viagem_id = ?",
            (v["id"],)).fetchone()[0]
        rob = conn.execute(
            "SELECT cb.carga_bordo FROM carga_bordo cb "
            "  JOIN escala e ON e.id = cb.escala_id "
            " WHERE e.viagem_id = ? AND e.tipo_escala = 'encerramento' "
            "   AND e.status <> 'cancelada'",
            (v["id"],)).fetchone()
        colunas.append({
            "carregado": carregado,
            "rob_carga": rob[0] if rob else None,
            "id": v["id"], "numero": v["numero"], "aberta": aberta,
            "sub": "em curso" if aberta else "encerrada " + formato_br(v["termino"])[:5],
            "inicio": v["inicio"], "termino": v["termino"],
            "duracao": _duracao_longa(valores["_duracao"]),
            "valores": valores,
        })
    return colunas


def _linhas_de_pernadas(colunas, orcado, agora_utc) -> list[tuple]:
    """[(grupo, [linha...])]: cada linha com uma celula por coluna e a referencia."""
    historico = [c["valores"] for c in colunas if not c["aberta"]]
    linhas = []
    for chave, grupo, nome, sub, _de, _para in pernadas.PERNADAS:
        ref_min, origem = pernadas.referencia(chave, orcado, historico)
        celulas = []
        for col in colunas:
            v = col["valores"][chave]
            comeco = col["valores"]["_de"].get(chave)
            if v is not None:
                d = pernadas.degrau(v, ref_min)
                celulas.append({"texto": _duracao(v), "classe": "acima-{}".format(d) if d else ""})
            elif col["aberta"] and comeco:
                # a pernada comecou e nao terminou: a hora ainda esta correndo
                decorrido = _entre(comeco, agora_utc)
                celulas.append({"texto": _duracao(decorrido) + " \u00b7 correndo", "classe": "curso"})
            else:
                celulas.append({"texto": "\u2014", "classe": "vazio"})
        linhas.append({
            "chave": chave, "grupo": grupo, "nome": nome, "sub": sub, "celulas": celulas,
            "ref": _duracao(ref_min) if ref_min is not None else "\u2014",
            "origem": origem,
        })
    return [(g, [l for l in linhas if l["grupo"] == g]) for g in pernadas.GRUPOS]


@app.get("/viagens", response_class=HTMLResponse)
def viagens_por_pernada(request: Request, navio: int = 0):
    conta = request.state.conta
    if conta["perfil"] == "navio":
        return RedirectResponse("/navio", 303)
    with closing(db.conectar()) as conn:
        navios = conn.execute(
            "SELECT id, nome_oficial FROM navio WHERE ativo = 1 ORDER BY id").fetchall()
        escolhido = next((n for n in navios if n["id"] == navio), navios[0] if navios else None)
        colunas = _colunas_de_viagens(conn, escolhido["id"]) if escolhido else []
        orcado = pernadas.premissas(conn)
    grupos = _linhas_de_pernadas(colunas, orcado, _agora_utc())
    return templates.TemplateResponse(request, "viagens.html", {
        "conta": conta, "navios": navios, "navio": escolhido,
        "colunas": colunas, "grupos": grupos,
        "encerradas": sum(1 for c in colunas if not c["aberta"]),
        "tem_orcado": bool(orcado),
    })


# ---------------------------------------------------------------------------
# Bunker: consumo por pernada e os abastecimentos
#
# A mesma grade de Viagens, em toneladas. Nada e digitado a mais: o ROB ja e
# lancado em todo marco, e o consumo de uma pernada e ROB de saida menos ROB
# de chegada, mais o abastecido no meio.
# ---------------------------------------------------------------------------

def _linhas_de_bunker(colunas, comb: str) -> list[tuple]:
    encerradas = [c for c in colunas if not c["aberta"]]
    linhas = []
    for chave, grupo, nome, sub, _de, _para in pernadas.PERNADAS:
        ref = bunker.media([c["consumo"][chave][comb] for c in encerradas])
        celulas = []
        for col in colunas:
            v = col["consumo"][chave][comb]
            if v is None:
                celulas.append({"texto": "—", "classe": "vazio"})
            else:
                d = bunker.degrau(v, ref)
                celulas.append({"texto": formato_mt(v),
                                "classe": "acima-{}".format(d) if d else ""})
        linhas.append({"chave": chave, "grupo": grupo, "nome": nome, "sub": sub,
                       "celulas": celulas,
                       "ref": formato_mt(ref) if ref is not None else "—"})
    return [(g, [l for l in linhas if l["grupo"] == g]) for g in pernadas.GRUPOS]


@app.get("/bunker", response_class=HTMLResponse)
def bunker_por_pernada(request: Request, navio: int = 0, comb: str = "vlsfo"):
    conta = request.state.conta
    if conta["perfil"] == "navio":
        return RedirectResponse("/navio", 303)
    if comb not in bunker.COMBUSTIVEIS:
        comb = "vlsfo"
    with closing(db.conectar()) as conn:
        navios = conn.execute(
            "SELECT id, nome_oficial FROM navio WHERE ativo = 1 ORDER BY id").fetchall()
        escolhido = next((n for n in navios if n["id"] == navio), navios[0] if navios else None)
        colunas, lista, leituras = [], [], []
        if escolhido:
            colunas = _colunas_de_viagens(conn, escolhido["id"])
            leituras = bunker.leituras(conn, escolhido["id"])
            for col in colunas:
                col["consumo"] = bunker.consumo_por_pernada(leituras, col["valores"])
                col["resumo"] = bunker.resumo_da_viagem(conn, col["id"], leituras, col["valores"])
            lista = bunker.abastecimentos(conn, escolhido["id"])
    return templates.TemplateResponse(request, "bunker.html", {
        "conta": conta, "navios": navios, "navio": escolhido, "comb": comb,
        "colunas": colunas, "grupos": _linhas_de_bunker(colunas, comb),
        "abastecimentos": lista,
    })


# ---------------------------------------------------------------------------
# Premissas: o orcamento de horas de cada pernada (so admin)
# ---------------------------------------------------------------------------

def _media_geral(conn) -> dict[str, tuple[str, int]]:
    """chave -> (media h/min, n) sobre TODAS as viagens encerradas da frota.

    E a dica ao lado do campo: o admin ve o que a frota vem fazendo antes de
    decidir o que orcar.
    """
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM viagem WHERE status = 'encerrada' ORDER BY id DESC LIMIT 60")]
    historico = [pernadas.calcular(conn, i) for i in ids]
    saida = {}
    for chave in pernadas.CHAVES:
        ref, _ = pernadas.referencia(chave, {}, historico)
        n = sum(1 for h in historico if h.get(chave) is not None)
        saida[chave] = (_duracao(ref) if ref is not None else "", n)
    return saida


def _horas_texto(horas) -> str:
    return "{:.1f}".format(horas).replace(".", ",") if horas is not None else ""


@app.get("/admin/premissas", response_class=HTMLResponse)
def admin_premissas(request: Request, erro: str = "", ok: str = ""):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    with closing(db.conectar()) as conn:
        orcado = pernadas.premissas(conn)
        media = _media_geral(conn)
    grupos = []
    for g in pernadas.GRUPOS:
        itens = []
        for chave, grupo, nome, sub, _de, _para in pernadas.PERNADAS:
            if grupo != g:
                continue
            itens.append({"chave": chave, "nome": nome, "sub": sub,
                          "horas": _horas_texto(orcado.get(chave)),
                          "media": media[chave][0], "n": media[chave][1]})
        grupos.append((g, itens))
    return templates.TemplateResponse(request, "admin_premissas.html", {
        "conta": request.state.conta, "grupos": grupos, "erro": erro, "ok": ok})


@app.post("/admin/premissas")
async def admin_premissas_salvar(request: Request):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    form = await request.form()
    valores = {}
    erros = []
    for chave in pernadas.CHAVES:
        bruto = (form.get("h_" + chave) or "").strip().replace(",", ".")
        if not bruto:
            valores[chave] = None            # vazio: volta a valer a media
            continue
        try:
            valores[chave] = float(bruto)
        except ValueError:
            erros.append("Horas inv\u00e1lidas em {}: {!r}".format(chave, bruto))
    if not erros:
        with closing(db.conectar()) as conn:
            erros = pernadas.gravar_premissas(conn, valores, por=request.state.conta["login"])
    destino = "/admin/premissas?" + ("erro=" + quote(" | ".join(erros)) if erros
                                      else "ok=" + quote("Premissas salvas."))
    return RedirectResponse(destino, 303)


# ---------------------------------------------------------------------------
# Administracao de contas
#
# Existe porque o plano free do Render nao tem terminal: o script de linha de
# comando funciona na maquina do Vinicius, mas em producao nao ha onde roda-lo.
# ---------------------------------------------------------------------------

def _so_admin(request: Request):
    return request.state.conta["perfil"] == "admin"


@app.get("/admin/contas", response_class=HTMLResponse)
def admin_contas(request: Request, erro: str = "", ok: str = ""):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    with closing(db.conectar()) as conn:
        lista = conn.execute(
            "SELECT c.login, c.nome_exibicao, c.perfil, c.ativo, n.nome_oficial AS navio "
            "  FROM conta c LEFT JOIN navio n ON n.id = c.navio_id "
            " ORDER BY c.perfil, c.login").fetchall()
        navios = conn.execute(
            "SELECT id, nome_oficial FROM navio WHERE ativo = 1 ORDER BY id").fetchall()
    return templates.TemplateResponse(request, "admin_contas.html", {
        "conta": request.state.conta, "lista": lista, "navios": navios,
        "erro": erro, "ok": ok})


@app.post("/admin/contas")
def admin_criar_conta(
    request: Request,
    login: str = Form(...),
    nome_exibicao: str = Form(...),
    perfil: str = Form(...),
    senha: str = Form(...),
    navio_id: str = Form(""),
):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    with closing(db.conectar()) as conn:
        criado, erros = contas.criar_conta(
            conn, login=login, senha=senha, nome_exibicao=nome_exibicao,
            perfil=perfil, navio_id=int(navio_id) if navio_id.strip() else None)
    if erros:
        return RedirectResponse(
            "/admin/contas?erro=" + quote(" | ".join(erros)), 303)
    return RedirectResponse(
        "/admin/contas?ok=" + quote("Conta {} criada.".format(criado)), 303)


@app.post("/admin/contas/senha")
def admin_trocar_senha(request: Request, login: str = Form(...), senha: str = Form(...)):
    if not _so_admin(request):
        return HTMLResponse("<h3>Apenas administradores.</h3>", status_code=403)
    with closing(db.conectar()) as conn:
        trocou, erros = contas.trocar_senha(conn, login, senha)
    destino = "/admin/contas?" + (
        "erro=" + quote(" | ".join(erros)) if erros
        else "ok=" + quote("Senha de {} trocada.".format(login)))
    return RedirectResponse(destino, 303)


# ---------------------------------------------------------------------------
# Analises: a tabela dinamica
#
# Base (viagens ou pernadas), dimensoes nas linhas e nas colunas, uma medida
# agregada nas celulas, filtros por dimensao. Tudo vive na query string —
# e por isso que um relatorio salvo e so um endereco com nome.
# ---------------------------------------------------------------------------

PADRAO_ANALISE = {"base": "viagens", "l": ["navio"], "c": ["mes"], "v": "descarregado",
                  "agg": "soma"}
# O que cada base mostra ao ser escolhida: a pergunta mais comum de cada uma.
PADRAO_POR_BASE = {
    "viagens": {"l": ["navio"], "c": ["mes"], "v": "descarregado", "agg": "soma"},
    "pernadas": {"l": ["grupo", "pernada"], "c": ["navio"], "v": "horas", "agg": "media"},
    "marcos": {"l": ["porto"], "c": ["navio"], "v": "fw", "agg": "media"},
}


def _ler_consulta(params) -> dict:
    """A consulta a partir da query string, ja validada contra o catalogo."""
    base = params.get("base") if params.get("base") in analises.BASES else "viagens"
    dims = {k for k, _r in analises.dimensoes_da(base)}
    meds = [k for k, _r in analises.medidas_da(base)]
    if not [k for k in params if k != "formato"]:
        q = dict(PADRAO_ANALISE)
        q["filtros"] = {"ano": {db.agora()[:4]}, "situacao": {"encerrada"}}
        return q
    linhas = [d for d in params.getlist("l") if d in dims]
    colunas = [d for d in params.getlist("c") if d in dims and d not in linhas]
    # Os seletores "+ campo" e "+ filtro" sem JavaScript: o servidor tambem entende.
    if params.get("nova_l") in dims and params.get("nova_l") not in linhas + colunas:
        linhas.append(params.get("nova_l"))
    if params.get("nova_c") in dims and params.get("nova_c") not in linhas + colunas:
        colunas.append(params.get("nova_c"))
    filtros = {}
    if params.get("novo_filtro") in dims:
        filtros[params.get("novo_filtro")] = set()
    for chave, valor in params.multi_items():
        if chave.startswith("f_") and chave[2:] in dims:
            filtros.setdefault(chave[2:], set())
            if valor:
                filtros[chave[2:]].add(valor)
    return {
        "base": base, "l": linhas, "c": colunas,
        "v": params.get("v") if params.get("v") in meds else meds[0],
        "agg": params.get("agg") if params.get("agg") in analises.AGREGACOES else "soma",
        "filtros": filtros,
    }


def _consulta_para_url(q: dict, **mudancas) -> str:
    """A query string de uma consulta, com alteracoes opcionais (para os links)."""
    q = {**q, **mudancas}
    partes = [("base", q["base"])] + [("l", d) for d in q["l"]] + [("c", d) for d in q["c"]]
    partes += [("v", q["v"]), ("agg", q["agg"])]
    for dim, valores in sorted(q["filtros"].items()):
        if valores:
            partes += [("f_" + dim, v) for v in sorted(valores)]
        else:
            partes.append(("f_" + dim, ""))
    return "/analises?" + urlencode(partes)


@app.get("/analises", response_class=HTMLResponse)
def analises_tela(request: Request, formato: str = ""):
    conta = request.state.conta
    if conta["perfil"] == "navio":
        return RedirectResponse("/navio", 303)
    q = _ler_consulta(request.query_params)

    with closing(db.conectar()) as conn:
        linhas = analises.carregar(conn, q["base"])
        salvos = conn.execute(
            "SELECT id, nome, consulta FROM relatorio_salvo ORDER BY nome").fetchall()
        portos_nome = conn.execute("SELECT nome, codigo FROM porto").fetchall()
    resultado = analises.pivotar(linhas, q["l"], q["c"], q["v"], q["agg"], q["filtros"])

    if formato == "csv":
        return Response(
            analises.csv(resultado, q["l"], q["v"], q["agg"]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="analise-corsair.csv"'})

    disponiveis = analises.valores_disponiveis(linhas, q["base"])
    nome, eixos = analises.titulo(q["v"], q["agg"], q["l"], q["c"])
    tipo_grafico = request.query_params.get("grafico") or "barras"
    if tipo_grafico == "linha" and not analises.eh_serie_historica(q["l"]):
        tipo_grafico = "barras"
    usadas = set(q["l"]) | set(q["c"])

    def tira_de(lista, d):
        return [x for x in lista if x != d]

    # Cada chip carrega o endereco que o remove (ou move); cada filtro, o que
    # liga e desliga cada valor. Sem JavaScript obrigatorio: sao links.
    chips_l = [{"chave": d, "rotulo": analises.DIMENSOES[d][0],
                "remover": _consulta_para_url(q, l=tira_de(q["l"], d)),
                "mover": _consulta_para_url(q, l=tira_de(q["l"], d), c=q["c"] + [d])}
               for d in q["l"]]
    chips_c = [{"chave": d, "rotulo": analises.DIMENSOES[d][0],
                "remover": _consulta_para_url(q, c=tira_de(q["c"], d)),
                "mover": _consulta_para_url(q, c=tira_de(q["c"], d), l=q["l"] + [d])}
               for d in q["c"]]
    filtros = []
    for dim, ligados in q["filtros"].items():
        sem = {k: v for k, v in q["filtros"].items() if k != dim}
        filtros.append({
            "chave": dim, "rotulo": analises.DIMENSOES[dim][0],
            "remover": _consulta_para_url(q, filtros=sem),
            "valores": [{
                "rotulo": v, "ligado": v in ligados,
                "url": _consulta_para_url(q, filtros={
                    **sem, dim: (ligados - {v}) if v in ligados else (ligados | {v})}),
            } for v in disponiveis.get(dim, [])],
        })
    return templates.TemplateResponse(request, "analises.html", {
        "conta": conta, "q": q, "resultado": resultado, "nome": nome, "eixos": eixos,
        "bases": analises.BASES, "agregacoes": analises.AGREGACOES,
        "dimensoes": analises.dimensoes_da(q["base"]),
        "medidas": analises.medidas_da(q["base"]),
        "livres": [(k, r) for k, r in analises.dimensoes_da(q["base"]) if k not in usadas],
        "sem_filtro": [(k, r) for k, r in analises.dimensoes_da(q["base"]) if k not in q["filtros"]],
        "chips_l": chips_l, "chips_c": chips_c, "filtros": filtros,
        "url_base": {b: _consulta_para_url(dict(PADRAO_ANALISE, filtros=q["filtros"]), base=b,
                                            **PADRAO_POR_BASE[b])
                     for b in analises.BASES},
        "grafico": (None if tipo_grafico == "0"
                    else analises.grafico_linha(resultado, q["v"], q["agg"]) if tipo_grafico == "linha"
                    else analises.grafico(resultado, q["v"], q["agg"])),
        "tipo_grafico": tipo_grafico,
        "pode_linha": analises.eh_serie_historica(q["l"]),
        "url_grafico": {t: _consulta_para_url(q) + "&grafico=" + t for t in ("barras", "linha", "0")},
        "consulta": _consulta_para_url(q), "csv": _consulta_para_url(q) + "&formato=csv",
        "salvos": salvos, "formatar": analises.formatar,
        "codigo_do_porto": {r[0]: r[1] for r in portos_nome},
        "pode_salvar": conta["perfil"] not in contas.PERFIS_SOMENTE_LEITURA,
        "regra_mes": "mes" in q["l"] + q["c"] or "trimestre" in q["l"] + q["c"] or "ano" in q["l"] + q["c"],
    })


@app.post("/analises/salvar")
def analises_salvar(request: Request, nome: str = Form(...), consulta: str = Form(...)):
    conta = request.state.conta
    if conta["perfil"] == "navio":
        return RedirectResponse("/navio", 303)
    nome = nome.strip()[:80]
    consulta = consulta if consulta.startswith("/analises?") else "/analises"
    if nome:
        with closing(db.conectar()) as conn:
            conn.execute(
                "INSERT INTO relatorio_salvo (nome, consulta, criado_por, criado_em) "
                "VALUES (?, ?, ?, ?)", (nome, consulta, conta["login"], db.agora()))
            conn.commit()
    return RedirectResponse(consulta, 303)


@app.post("/analises/apagar")
def analises_apagar(request: Request, id: int = Form(...), consulta: str = Form("/analises")):
    conta = request.state.conta
    if conta["perfil"] == "navio":
        return RedirectResponse("/navio", 303)
    with closing(db.conectar()) as conn:
        conn.execute("DELETE FROM relatorio_salvo WHERE id = ?", (id,))
        conn.commit()
    return RedirectResponse(consulta if consulta.startswith("/analises") else "/analises", 303)


# ---------------------------------------------------------------------------
# Importar viagens passadas (supervisao): planilha das supervisoras ou CSV
#
# Dois passos, sempre: ler e mostrar o relatorio; so a confirmacao grava. O
# arquivo fica guardado entre os dois passos, por chave aleatoria, e e apagado
# ao gravar. Viagem que ja existe nao e tocada.
# ---------------------------------------------------------------------------

def _pode_importar(conta) -> bool:
    return conta["perfil"] in ("supervisor", "admin")


def _relatorio_importacao(conn, viagens_por_navio, problemas) -> dict:
    itens = importacao.plano(conn, viagens_por_navio)
    return {
        "resumo": hp_resumo(viagens_por_navio), "plano": itens, "problemas": problemas,
        "erros": sum(1 for p in problemas if p.nivel == "erro"),
        "novas": sum(1 for i in itens if i["situacao"] == "nova"),
        "existentes": sum(1 for i in itens if i["situacao"] == "existe"),
        "conflitos": sum(1 for i in itens if i["situacao"] == "conflito"),
    }


def hp_resumo(viagens_por_navio):
    from ferramentas import historico_planilha
    return historico_planilha.resumo(viagens_por_navio)


@app.get("/importar", response_class=HTMLResponse)
def importar_tela(request: Request):
    conta = request.state.conta
    if not _pode_importar(conta):
        return RedirectResponse("/painel" if conta["perfil"] != "navio" else "/navio", 303)
    return templates.TemplateResponse(request, "importar.html", {
        "conta": conta, "cabecalho": importacao.CABECALHO_MODELO, "exemplo": importacao.EXEMPLO_MODELO[:6]})


@app.get("/importar/modelo.csv")
def importar_modelo(request: Request):
    if not _pode_importar(request.state.conta):
        return RedirectResponse("/painel", 303)
    return Response(importacao.modelo_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="modelo-viagens-corsair.csv"'})


@app.post("/importar", response_class=HTMLResponse)
async def importar_ler(request: Request, arquivo: UploadFile = File(...)):
    conta = request.state.conta
    if not _pode_importar(conta):
        return RedirectResponse("/painel", 303)
    nome = arquivo.filename or "arquivo"
    if not nome.lower().endswith((".xlsx", ".xlsm", ".csv")):
        return templates.TemplateResponse(request, "importar.html", {
            "conta": conta, "cabecalho": importacao.CABECALHO_MODELO,
            "exemplo": importacao.EXEMPLO_MODELO[:6], "erro": "Envie um .xlsx ou um .csv."})
    chave = importacao.guardar(nome, await arquivo.read())
    with closing(db.conectar()) as conn:
        try:
            viagens_lidas, problemas = importacao.ler(importacao.caminho_de(chave), conn)
        except Exception as exc:  # noqa: BLE001 — arquivo alheio: o erro vira relatorio
            importacao.descartar(chave)
            return templates.TemplateResponse(request, "importar.html", {
                "conta": conta, "cabecalho": importacao.CABECALHO_MODELO,
                "exemplo": importacao.EXEMPLO_MODELO[:6],
                "erro": "Não consegui ler o arquivo: {}".format(exc)})
        relatorio = _relatorio_importacao(conn, viagens_lidas, problemas)
    return templates.TemplateResponse(request, "importar.html", {
        "conta": conta, "relatorio": relatorio, "chave": chave, "nome_arquivo": nome})


@app.post("/importar/confirmar", response_class=HTMLResponse)
def importar_confirmar(request: Request, chave: str = Form(...)):
    conta = request.state.conta
    if not _pode_importar(conta):
        return RedirectResponse("/painel", 303)
    caminho = importacao.caminho_de(chave)
    if caminho is None:
        return RedirectResponse("/importar", 303)
    with closing(db.conectar()) as conn:
        viagens_lidas, problemas = importacao.ler(caminho, conn)
        if any(p.nivel == "erro" for p in problemas):
            relatorio = _relatorio_importacao(conn, viagens_lidas, problemas)
            return templates.TemplateResponse(request, "importar.html", {
                "conta": conta, "relatorio": relatorio, "chave": chave, "nome_arquivo": caminho.name})
        resultado = importacao.importar(conn, viagens_lidas, por=conta["login"])
    importacao.descartar(chave)
    return templates.TemplateResponse(request, "importar.html", {"conta": conta, "resultado": resultado})


# ---------------------------------------------------------------------------
# O Statement of Facts pela API — mesmo contrato do marco: JSON, 422 no dado
# invalido para a fila nao repetir, 403 em parada de outro navio.
# ---------------------------------------------------------------------------

def _escala_do_navio(conn, conta, escala_id):
    escala = conn.execute(
        "SELECT e.id, vg.navio_id FROM escala e JOIN viagem vg ON vg.id = e.viagem_id WHERE e.id = ?",
        (escala_id,)).fetchone()
    if escala is None:
        return None, JSONResponse({"ok": False, "erros": ["Escala não encontrada."]}, 404)
    if conta["perfil"] == "navio" and escala["navio_id"] != conta["navio_id"]:
        return None, JSONResponse({"ok": False, "erros": ["Escala de outro navio."]}, 403)
    return escala, None


@app.post("/api/sof/marco")
async def api_sof_marco(request: Request):
    corpo = await request.json()
    conta = request.state.conta
    with closing(db.conectar()) as conn:
        escala, recusa = _escala_do_navio(conn, conta, corpo.get("escala_id"))
        if recusa:
            return recusa
        _, erros = sof.registrar_marco(
            conn, escala["id"], tipo=corpo.get("tipo"), hora_local=corpo.get("hora_local"),
            offset=corpo.get("offset"), nome_responsavel=corpo.get("nome_responsavel", ""),
            registrado_por=conta["login"], observacao=corpo.get("observacao"))
    if erros:
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)
    return {"ok": True}


@app.post("/api/sof/dados")
async def api_sof_dados(request: Request):
    corpo = await request.json()
    conta = request.state.conta
    with closing(db.conectar()) as conn:
        escala, recusa = _escala_do_navio(conn, conta, corpo.get("escala_id"))
        if recusa:
            return recusa
        _, erros = sof.registrar_dados(
            conn, escala["id"], dados=corpo.get("dados") or {},
            nome_responsavel=corpo.get("nome_responsavel", ""), registrado_por=conta["login"])
    if erros:
        return JSONResponse({"ok": False, "erros": erros}, status_code=422)
    return {"ok": True}
