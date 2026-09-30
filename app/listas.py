# -*- coding: utf-8 -*-
"""As listas que as telas oferecem em seletores — num lugar só, para mudar sem caçar.

Tudo o que aparece como opção fixa numa tela do Corsair (o que não vem do
banco, como os portos) mora aqui. Acrescentar uma opção é acrescentar uma
linha; nenhum template precisa mudar. A ordem aqui é a ordem na tela.

O que vem do BANCO e não daqui: portos (tabela `porto` + `porto_alias`),
navios, rota-modelo e premissas.
"""

# Retirada de lixo no marco (set/2026). O valor vai para evento.lixo como está.
LIXO = (
    ("", "—"),
    ("Solido", "Sólido"),
    ("Liquido", "Líquido"),
    ("Solido e Liquido", "Sólido e líquido"),
)

# Por que uma parada adicional existe. A chave é o `motivo` da escala e tem
# de existir em dominio.MOTIVOS.
MOTIVOS_PARADA_ADICIONAL = (
    ("bunker", "Bunker — abastecimento"),
    ("espera_mare", "Espera de maré"),
    ("docagem", "Docagem"),
    ("outro", "Outro"),
)

# Atraca? A chave é o `tipo_escala` (dominio.TIPOS_ESCALA, menos abertura).
TIPOS_PARADA_ADICIONAL = (
    ("fundeio", "Não — fica fundeado"),
    ("operacional", "Sim — atraca"),
    ("passagem", "Só passagem"),
)


# Quantas vezes, em média, o pirata aparece a cada clique. 10.000 = uma em dez mil.
PIRATA_UMA_EM = 10000
