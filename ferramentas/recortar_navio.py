# -*- coding: utf-8 -*-
"""Tira o fundo branco (ou o xadrez de "transparencia") SEM comer o branco de dentro.

Um recorte ingenuo — "branco vira transparente" — apagaria o castelo do navio
junto com o fundo. Aqui o fundo e o que se alcanca a partir da BORDA da imagem
andando so por pixels quase brancos: o branco de dentro do casco fica, porque
esta cercado de cor e a inundacao nao chega nele.

Roda a mao, quando a arte mudar:

    python ferramentas/recortar_navio.py [origem] [destino]
"""
from __future__ import annotations

import pathlib
import sys
from collections import deque

MARCA = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "marca"

# Distancia ate o branco puro, somada nos tres canais. 60 pega o branco e o
# quase-branco do anti-serrilhado da borda; nao pega o cinza do costado.
LIMIAR = 60
MARGEM = 6  # pixels transparentes ao redor, para nada encostar na moldura


def quase_branco(pixel) -> bool:
    r, g, b = pixel[:3]
    return (255 - r) + (255 - g) + (255 - b) <= LIMIAR


def main(origem: pathlib.Path, destino: pathlib.Path) -> int:
    from PIL import Image

    im = Image.open(origem).convert("RGBA")
    w, h = im.size
    px = im.load()

    fundo = bytearray(w * h)  # 1 = faz parte do fundo
    fila = deque()
    for x in range(w):
        fila.append((x, 0)); fila.append((x, h - 1))
    for y in range(h):
        fila.append((0, y)); fila.append((w - 1, y))

    while fila:
        x, y = fila.popleft()
        i = y * w + x
        if fundo[i] or not quase_branco(px[x, y]):
            continue
        fundo[i] = 1
        if x > 0:     fila.append((x - 1, y))
        if x < w - 1: fila.append((x + 1, y))
        if y > 0:     fila.append((x, y - 1))
        if y < h - 1: fila.append((x, y + 1))

    # A borda do casco tem pixels meio-brancos do anti-serrilhado. Em vez de
    # cortar seco, a opacidade deles acompanha o quanto se afastam do branco —
    # e o que evita o "halo" branco ao redor do navio sobre fundo escuro.
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if fundo[y * w + x]:
                px[x, y] = (r, g, b, 0)
            else:
                dist = (255 - r) + (255 - g) + (255 - b)
                if dist < LIMIAR * 2:
                    px[x, y] = (r, g, b, min(255, int(255 * dist / (LIMIAR * 2))))

    caixa = im.getbbox()
    if caixa is None:
        print("A imagem ficou vazia — o limiar comeu tudo.")
        return 1
    im = im.crop(caixa)
    tela = Image.new("RGBA", (im.width + 2 * MARGEM, im.height + 2 * MARGEM), (0, 0, 0, 0))
    tela.paste(im, (MARGEM, MARGEM), im)
    tela.save(destino, optimize=True)

    transparentes = sum(1 for v in fundo if v)
    print("  {} -> {}x{}, {} bytes; {:.0%} da imagem era fundo".format(
        destino.name, tela.width, tela.height, destino.stat().st_size,
        transparentes / (w * h)))
    return 0


if __name__ == "__main__":
    origem = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else MARCA / "navio.png"
    destino = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else origem
    # Terceiro argumento: o limiar. O navio precisou de 60 (fundo branco); a
    # logo da Alcoa veio com o xadrez cinza de "transparencia" pintado, e o
    # cinza esta a ~150 do branco — precisa de 200 para sair inteiro.
    if len(sys.argv) > 3:
        LIMIAR = int(sys.argv[3])
    sys.exit(main(origem, destino))
