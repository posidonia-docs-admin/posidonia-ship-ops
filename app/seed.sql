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

-- Prefixo do codigo de viagem. UPDATE e nao INSERT porque o INSERT OR IGNORE
-- acima nao toca em linha que ja existe — bancos criados antes desta coluna
-- ficariam com prefixo nulo para sempre.
UPDATE navio SET prefixo = 'APT' WHERE id = 1 AND COALESCE(prefixo, '') = '';
UPDATE navio SET prefixo = 'APN' WHERE id = 2 AND COALESCE(prefixo, '') = '';
UPDATE navio SET prefixo = 'ACM' WHERE id = 3 AND COALESCE(prefixo, '') = '';
UPDATE navio SET prefixo = 'ACR' WHERE id = 4 AND COALESCE(prefixo, '') = '';

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
    ('abertura',    'sailing', 4),
    -- escala que FECHA a viagem: nao tem sailing. O sailing seguinte e o
    -- PRIMEIRO lancamento da proxima viagem, nao um resto desta.
    ('encerramento','arrival', 1),
    ('encerramento','berth',   2),
    ('encerramento','unberth', 3);

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

-- A rota padrao: SEIS etapas.
--
-- A viagem COMECA no Sailing de Alumar e TERMINA no Unberth de Alumar. O
-- Sailing e o PRIMEIRO lancamento da viagem — nunca fica preso na anterior.
-- Cada marco continua gravado uma vez so; o que muda e a qual viagem ele
-- pertence, e isso segue o modo como a operacao pensa a viagem.
--
-- `condicao` = o que o navio esta FAZENDO. `motivo` = por que parou aqui.
-- Em Barra Norte: condicao `laden`, motivo `espera_mare`.
--
-- DELETE + INSERT, e nao INSERT OR IGNORE: a rota e configuracao derivada do
-- codigo, nao dado do usuario. Reescrever a cada arranque garante que um banco
-- antigo receba a estrutura nova — nenhuma escala ja criada e tocada, porque
-- escala e copia, nao referencia.
DELETE FROM rota_etapa WHERE rota_modelo_id = 1;

INSERT INTO rota_etapa
    (rota_modelo_id, ordem, codigo_porto, tipo_escala, sentido, motivo, condicao,
     observacao) VALUES
    (1, 1, 'ALUMAR',      'abertura',     'na',      'abertura',     'ballast',
     'Saida de Alumar. E o PRIMEIRO lancamento da viagem. So o Sailing.'),
    (1, 2, 'FAZENDINHA',  'passagem',     'subida',  'passagem',     'ballast',
     'Passagem no trecho fluvial. Nao atraca. Segue vazio para Juruti.'),
    (1, 3, 'JURUTI',      'operacional',  'subida',  'carregamento', 'loading',
     'Carrega bauxita.'),
    (1, 4, 'FAZENDINHA',  'passagem',     'descida', 'passagem',     'laden',
     'Mesma Fazendinha da etapa 2, agora na descida e carregado.'),
    (1, 5, 'BARRA_NORTE', 'passagem',     'descida', 'espera_mare',  'laden',
     'Parada por conta da mare. Nao atraca. Continua carregado.'),
    (1, 6, 'ALUMAR',      'encerramento', 'descida', 'descarga',     'discharging',
     'Descarrega. O Unberth e o ULTIMO lancamento da viagem.');

-- Escalas ja criadas antes da coluna existir: herdam a condicao da etapa que as
-- gerou. escala.ordem = rota_etapa.ordem * 10 (ver PASSO_ORDEM em viagens.py).
-- Sem isto, a viagem que estava aberta em producao ficaria sem condicao para
-- sempre — e e justamente a que o comandante vai preencher.
UPDATE escala SET condicao = (
        SELECT re.condicao FROM rota_etapa re
         WHERE re.rota_modelo_id = (SELECT vg.rota_modelo_id FROM viagem vg
                                     WHERE vg.id = escala.viagem_id)
           AND re.ordem = escala.ordem / 10)
 WHERE condicao IS NULL AND origem = 'modelo';

UPDATE escala SET condicao = 'ballast'
 WHERE condicao IS NULL AND origem = 'abertura';
