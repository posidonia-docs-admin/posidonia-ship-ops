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

# Os marcos do Statement of Facts, além dos quatro do circuito (Arrival, Berth,
# Unberth, Sailing). Chave, rótulo, e em que tipo de parada fazem sentido
# ("operacional"/"encerramento" = atraca; "todas" = qualquer parada).
# Lidos do SOF da APT26018 (Juruti, set/2026).
MARCOS_SOF = (
    ("pratico_a_bordo", "Prático a bordo", "todas"),
    ("nor_tendered", "NOR tendered (aviso de prontidão)", "atraca"),
    ("nor_accepted", "NOR accepted", "atraca"),
    ("rebocador_fast_atracacao", "Rebocadores fast para atracação", "atraca"),
    ("primeiro_cabo", "Primeiro cabo em terra", "atraca"),
    ("rebocador_off_atracacao", "Rebocadores cast off (atracação)", "atraca"),
    ("liberado_operacao", "Navio liberado para operação", "atraca"),
    ("draft_inicial", "Draft survey inicial", "atraca"),
    ("inicio_operacao", "Início da operação", "atraca"),
    ("fim_operacao", "Fim da operação", "atraca"),
    ("draft_final", "Draft survey final", "atraca"),
    ("documentos_a_bordo", "Documentos de carga a bordo", "atraca"),
    ("rebocador_fast_desatracacao", "Rebocadores fast para desatracação", "atraca"),
    ("inicio_manobra_saida", "Início da manobra de desatracação", "atraca"),
    ("todos_cabos_soltos", "Todos os cabos soltos", "atraca"),
    ("rebocador_off_desatracacao", "Rebocadores cast off (desatracação)", "atraca"),
    ("pratico_desembarcou", "Prático desembarcou", "todas"),
    ("barra_norte_cruzada", "Cruzou a Barra Norte", "todas"),
)

# Dados numéricos do SOF numa parada que atraca. Chave, rótulo, unidade.
DADOS_SOF = (
    ("draft_fwd_atracacao", "Calado a vante na atracação", "m"),
    ("draft_aft_atracacao", "Calado a ré na atracação", "m"),
    ("draft_fwd_saida", "Calado a vante na saída", "m"),
    ("draft_aft_saida", "Calado a ré na saída", "m"),
    ("taxa_carga", "Taxa de carga por hora", "MT/h"),
    ("tempo_trabalhado", "Tempo trabalhado", "h"),
    ("tempo_parado", "Tempo parado", "h"),
    ("carga_remanescente", "Carga remanescente a bordo", "MT"),
)
PORÕES = 8   # porão 1..8: carga por porão, em MT

# Quantas vezes, em média, o pirata aparece a cada clique. 10.000 = uma em dez mil.
PIRATA_UMA_EM = 10000
