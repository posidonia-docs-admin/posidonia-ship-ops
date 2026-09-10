"""Cria contas de acesso.

    python scripts/criar_conta.py navio.pathfinder "Amazon Pathfinder" navio --navio 1
    python scripts/criar_conta.py vlo "Vinicius" supervisor

A senha e pedida no terminal e nunca vai para o historico do shell nem para o
repositorio. Sem senha no codigo — a licao mais cara do Sistema Emissor.
"""
import argparse
import getpass
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import contas, db  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("login")
p.add_argument("nome")
p.add_argument("perfil", choices=contas.PERFIS)
p.add_argument("--navio", type=int, default=None, help="id do navio (perfil 'navio')")
args = p.parse_args()

senha = getpass.getpass("Senha (min. 8 caracteres): ")
if senha != getpass.getpass("Repita: "):
    raise SystemExit("As senhas nao conferem.")

db.inicializar()
with closing(db.conectar()) as conn:
    criado, erros = contas.criar_conta(
        conn, login=args.login, senha=senha, nome_exibicao=args.nome,
        perfil=args.perfil, navio_id=args.navio)

if erros:
    raise SystemExit("\n".join("- " + e for e in erros))
print("Conta {} criada ({}).".format(criado, args.perfil))
