# A marca da Posidonia no Corsair

Os arquivos vieram do Vinicius em 11/set/2026. Onde cada um entra:

| arquivo | onde aparece |
|---|---|
| `tridente.png` | so como ORIGEM dos icones — nao e servido direto |
| `icone-16.png` · `icone-32.png` | a aba do navegador |
| `icone-180.png` | atalho na tela inicial do celular |
| `posidonia.png` | a tela de entrar, sobre fundo claro |
| `posidonia-branca.png` | a barra lateral, sobre o navy |
| `navio.png` | pe da barra lateral e tela de entrar — fundo RECORTADO, ver abaixo |

## Duas coisas que nao sao gosto, sao regra

**A barra lateral usa a versao BRANCA.** A colorida tem o tridente em navy;
sobre o fundo navy da barra ele desaparece e sobra a palavra POSIDONIA solta.

**Os icones sao CORTADOS, nao redimensionados pelo navegador.** Servir o
tridente de 937px como favicon funciona, mas manda 53 KB para desenhar 16
pixels. Depois de trocar a arte:

    python ferramentas/gerar_icones.py

O endereco dos estaticos carrega a impressao digital do conteudo, e ela varre
esta pasta tambem — arte nova e endereco novo, sem ninguem limpar cache.

## O navio veio com fundo branco pintado

Nao era transparencia — era branco de verdade, e a superestrutura do navio
tambem e branca. Um recorte "branco vira transparente" apagaria o castelo.
`ferramentas/recortar_navio.py` inunda a partir da BORDA: so o branco que
encosta na moldura sai; o branco cercado de cor fica. Se a arte mudar:

    python ferramentas/recortar_navio.py

Os quatro Amazon sao navios-irmaos (confirmado pelo Vinicius em 12/set/2026),
entao uma unica silhueta serve para os quatro.
