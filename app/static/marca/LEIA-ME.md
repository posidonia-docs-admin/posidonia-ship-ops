# A marca da Posidonia no Corsair

Os arquivos vieram do Vinicius em 11/set/2026. Onde cada um entra:

| arquivo | onde aparece |
|---|---|
| `tridente.png` | so como ORIGEM dos icones — nao e servido direto |
| `icone-16.png` · `icone-32.png` | a aba do navegador |
| `icone-180.png` | atalho na tela inicial do celular |
| `posidonia.png` | a tela de entrar, sobre fundo claro |
| `posidonia-branca.png` | a barra lateral, sobre o navy |
| `navio.png` | pe da barra lateral e tela de entrar |

## Duas coisas que nao sao gosto, sao regra

**A barra lateral usa a versao BRANCA.** A colorida tem o tridente em navy;
sobre o fundo navy da barra ele desaparece e sobra a palavra POSIDONIA solta.

**Os icones sao CORTADOS, nao redimensionados pelo navegador.** Servir o
tridente de 937px como favicon funciona, mas manda 53 KB para desenhar 16
pixels. Depois de trocar a arte:

    python ferramentas/gerar_icones.py

O endereco dos estaticos carrega a impressao digital do conteudo, e ela varre
esta pasta tambem — arte nova e endereco novo, sem ninguem limpar cache.
