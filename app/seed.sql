-- Posidonia Ship Ops — carga inicial.
-- Idempotente e NAO destrutiva: so INSERT OR IGNORE. Nunca apaga o que ja existe.

-- ---------------------------------------------------------------------------
-- Os 4 navios Amazon (Ship Management — Alcoa).
-- IMO fica NULO de proposito: numero inventado parece certo e por isso e pior
-- que numero ausente. Preencher quando a operacao confirmar.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO navio (id, imo, nome_oficial, ativo, observacao) VALUES
    (1, NULL, 'AMAZON PATHFINDER', 1, 'Alcoa — Ship Management'),
    (2, NULL, 'AMAZON PIONEER',    1, 'Alcoa — Ship Management'),
    (3, NULL, 'AMAZON COMMANDER',  1, 'Alcoa — Ship Management'),
    (4, NULL, 'AMAZON COURAGE',    1, 'Alcoa — Ship Management');

INSERT OR IGNORE INTO navio_alias (alias, navio_id) VALUES
    ('AMAZON PATHFINDER', 1), ('PATHFINDER', 1),
    ('AMAZON PIONEER',    2), ('PIONEER',    2),
    ('AMAZON COMMANDER',  3), ('COMMANDER',  3),
    ('AMAZON COURAGE',    4), ('COURAGE',    4);

-- ---------------------------------------------------------------------------
-- Portos do circuito. UN/LOCODE nulo pela mesma razao do IMO.
-- Toda a rota fica em UTC-3 (PA, AP e MA).
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO porto (codigo, nome, un_locode, uf, pais, offset_padrao, ativo, observacao) VALUES
    ('ALUMAR',      'Alumar',      NULL, 'MA', 'BR', '-03:00', 1,
     'Terminal da refinaria, Sao Luis. Descarga de bauxita. Abre e fecha a viagem.'),
    ('JURUTI',      'Juruti',      NULL, 'PA', 'BR', '-03:00', 1,
     'Mina, rio Amazonas. Carrega bauxita.'),
    ('FAZENDINHA',  'Fazendinha',  NULL, 'AP', 'BR', '-03:00', 1,
     'Passagem no trecho fluvial. Nao atraca. Aparece duas vezes por viagem. '
     || 'Tambem chamada de Macapa — mesma parada, ver porto_alias.'),
    ('BARRA_NORTE', 'Barra Norte', NULL, 'AP', 'BR', '-03:00', 1,
     'Parada por conta da mare. Nao atraca.'),
    ('ICOARACI',    'Icoaraci',    NULL, 'PA', 'BR', '-03:00', 1,
     'Eventual, para bunker. Entrada por Mosqueiro. FUNDEIA — nao atraca (confirmado 10/set/2026).'),
    ('ITAQUI',      'Itaqui',      NULL, 'MA', 'BR', '-03:00', 1,
     'Eventual, fundeio para bunker. Terminal distinto de Alumar — confirmar com a operacao.');

INSERT OR IGNORE INTO porto_alias (alias, codigo_porto) VALUES
    ('ALUMAR', 'ALUMAR'), ('PORTO DO ALUMAR', 'ALUMAR'), ('SAO LUIS - ALUMAR', 'ALUMAR'),
    ('JURUTI', 'JURUTI'), ('PORTO DE JURUTI', 'JURUTI'),
    -- Macapa e a mesma parada que Fazendinha (confirmado 10/set/2026): um porto so,
    -- com as duas grafias resolvendo para ele. Cadastrar dois duplicaria a escala.
    ('FAZENDINHA', 'FAZENDINHA'), ('MACAPA', 'FAZENDINHA'), ('MACAPA/AP', 'FAZENDINHA'),
    ('BARRA NORTE', 'BARRA_NORTE'), ('BARRA-NORTE', 'BARRA_NORTE'),
    ('ICOARACI', 'ICOARACI'),
    ('ITAQUI', 'ITAQUI'), ('ITQ', 'ITAQUI'), ('IQI', 'ITAQUI'), ('PORTO DO ITAQUI', 'ITAQUI');

-- ---------------------------------------------------------------------------
-- Quais marcos cada tipo de escala pede.
-- `fundeio` e `passagem` capturam igual (so arrival e sailing), mas se
-- distinguem na analise: fundeado esperando nao e a mesma coisa que so passou.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO marco_exigido (tipo_escala, tipo_evento, ordem) VALUES
    ('operacional', 'arrival', 1),
    ('operacional', 'berth',   2),
    ('operacional', 'unberth', 3),
    ('operacional', 'sailing', 4),
    ('fundeio',     'arrival', 1),
    ('fundeio',     'sailing', 4),
    ('passagem',    'arrival', 1),
    ('passagem',    'sailing', 4),
    -- escala de abertura: existe so para registrar a saida da primeira
    -- viagem de um navio, quando nao ha viagem anterior de onde herdar o sailing
    ('abertura',    'sailing', 4);

-- ---------------------------------------------------------------------------
-- A rota padrao: Alumar -> Juruti -> Alumar.
--
-- Sao CINCO etapas, nao seis. O `sailing` de Alumar que abre a viagem pertence
-- a escala de Alumar da viagem ANTERIOR — repeti-lo aqui criaria a mesma escala
-- fisica duas vezes. A viagem se abre por evento-ancora, nao por etapa.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO rota_modelo (id, nome, descricao, ativo) VALUES
    (1, 'Alumar <-> Juruti (bauxita)',
     'Circuito padrao dos 4 Amazon. Abre no sailing de Alumar da viagem '
     || 'anterior e fecha no unberth de Alumar desta viagem.', 1);

INSERT OR IGNORE INTO rota_etapa
    (rota_modelo_id, ordem, codigo_porto, tipo_escala, sentido, motivo, observacao) VALUES
    (1, 1, 'FAZENDINHA',  'passagem',    'subida',  'passagem',
     'Passagem no trecho fluvial. Nao atraca.'),
    (1, 2, 'JURUTI',      'operacional', 'subida',  'carregamento',
     'Carrega bauxita.'),
    (1, 3, 'FAZENDINHA',  'passagem',    'descida', 'passagem',
     'Mesma Fazendinha da etapa 1, agora na descida.'),
    (1, 4, 'BARRA_NORTE', 'passagem',    'descida', 'espera_mare',
     'Parada por conta da mare. Nao atraca.'),
    (1, 5, 'ALUMAR',      'operacional', 'descida', 'descarga',
     'Descarrega. Seu unberth FECHA esta viagem; seu sailing ABRE a proxima.');
