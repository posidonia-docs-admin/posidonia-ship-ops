# -*- coding: utf-8 -*-
"""Corta os tamanhos de icone a partir do tridente em alta.

Roda A MAO, quando a arte mudar — nao no arranque do app. Os arquivos gerados
vao para o repositorio: o container nao precisa de Pillow, e um redimensionamento
no boot seria trabalho repetido a cada deploy para um resultado sempre igual.

    python ferramentas/gerar_icones.py
"""
from __future__ import annotations

import pathlib
import sys

MARCA = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "marca"
ORIGEM = MARCA / "tridente.png"

# 16 e 32 para a aba; 180 para o atalho na tela inicial do iPhone, que e como
# o comandante vai abrir o sistema no celular.
TAMANHOS = (16, 32, 180)


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow nao instalado. `pip install Pillow` e rode de novo.")
        return 1

    if not ORIGEM.exists():
        print("Falta {}".format(ORIGEM))
        return 1

    with Image.open(ORIGEM) as arte:
        arte = arte.convert("RGBA")
        lado = max(arte.size)
        # A arte pode nao ser exatamente quadrada. Centralizar numa tela
        # quadrada e transparente evita o icone sair esticado.
        tela = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
        tela.paste(arte, ((lado - arte.width) // 2, (lado - arte.height) // 2), arte)

        for tamanho in TAMANHOS:
            destino = MARCA / "icone-{}.png".format(tamanho)
            tela.resize((tamanho, tamanho), Image.LANCZOS).save(destino, optimize=True)
            print("  {}  {} bytes".format(destino.name, destino.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
