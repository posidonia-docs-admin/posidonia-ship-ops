"""Autenticacao: senha por conta, sessao em cookie assinado.

Diferente do Sistema Emissor, que usa uma senha unica compartilhada escrita no
repositorio: aqui os usuarios sao EXTERNOS (tripulacao, que troca), entao cada
conta tem senha propria com hash. Nao ha tabela de sessao — o cookie carrega a
identidade assinada com HMAC, o que mantem o app sem estado.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from . import config

COOKIE_SESSAO = "shipops_sessao"

# Sessao longa de proposito: o comandante nao pode ser deslogado no meio de um
# lancamento, muito menos a bordo com a conexao oscilando.
DURACAO_SESSAO = 30 * 24 * 60 * 60  # 30 dias

# Parametros do scrypt. n=2**14 e o equilibrio usual entre custo e latencia.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def _de_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------

def hash_senha(senha: str) -> str:
    """Devolve 'scrypt$n$r$p$sal$hash'. O sal e novo a cada chamada."""
    sal = secrets.token_bytes(16)
    bruto = hashlib.scrypt(
        senha.encode("utf-8"), salt=sal,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _b64(sal), _b64(bruto))


def verificar_senha(senha: str, guardado: str) -> bool:
    """Comparacao em tempo constante. Formato invalido devolve False, nao excecao."""
    try:
        marca, n, r, p, sal_b64, hash_b64 = guardado.split("$")
        if marca != "scrypt":
            return False
        calculado = hashlib.scrypt(
            senha.encode("utf-8"), salt=_de_b64(sal_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(_de_b64(hash_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculado, _de_b64(hash_b64))


# ---------------------------------------------------------------------------
# Sessao
# ---------------------------------------------------------------------------

def assinar_sessao(login: str) -> str:
    bruto = "{}|{}".format(login, int(time.time())).encode("utf-8")
    assinatura = hmac.new(
        config.secret_key().encode("utf-8"), bruto, hashlib.sha256).digest()
    return "{}.{}".format(_b64(bruto), _b64(assinatura))


def verificar_sessao(cookie: str) -> str | None:
    """Devolve o login, ou None. Assinatura ma e sessao velha caem no mesmo lugar."""
    if not cookie or "." not in cookie:
        return None
    try:
        parte_bruta, parte_assinatura = cookie.split(".", 1)
        bruto = _de_b64(parte_bruta)
        esperada = hmac.new(
            config.secret_key().encode("utf-8"), bruto, hashlib.sha256).digest()
        if not hmac.compare_digest(esperada, _de_b64(parte_assinatura)):
            return None
        login, carimbo = bruto.decode("utf-8").rsplit("|", 1)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if time.time() - int(carimbo) > DURACAO_SESSAO:
        return None
    return login
