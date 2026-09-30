-- Posidonia Ship Ops — schema do banco operacional.
-- Idempotente: pode rodar quantas vezes quiser.
--
-- Ao mudar QUALQUER estrutura aqui, incremente VERSAO_SCHEMA em db.py e o
-- PRAGMA user_version na ultima linha. Sem isso o banco antigo sobrevive ao
-- CREATE TABLE IF NOT EXISTS e da erro obscuro depois.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- navio — os 4 Amazon. O IMO fica nulo ate a operacao confirmar: numero de IMO
-- inventado e pior que numero ausente, porque parece certo.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS navio (
    id           INTEGER PRIMARY KEY,
    imo          INTEGER UNIQUE,
    nome_oficial TEXT NOT NULL UNIQUE,          -- MAIUSCULAS, sem acento
    prefixo      TEXT,                          -- APT, APN, ACM, ACR
    ativo        INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    observacao   TEXT
);

-- O indice unico de `prefixo` vive em _INDICES_POSTERIORES (db.py), nao aqui:
-- este script roda ANTES da migracao, e num banco antigo a coluna ainda nao
-- existe. Indexar coluna inexistente derruba o arranque inteiro.

CREATE TABLE IF NOT EXISTS navio_alias (
    alias    TEXT PRIMARY KEY,                  -- MAIUSCULAS, sem acento
    navio_id INTEGER NOT NULL REFERENCES navio(id)
);

-- ---------------------------------------------------------------------------
-- porto — a entidade que nao existia em lugar nenhum do acervo.
--
-- `codigo` e nosso, estavel e nosso para sempre. `un_locode` e o codigo
-- internacional e fica NULO ate a operacao confirmar cada um — a mesma regra do
-- IMO. O comandante escolhe de lista; ninguem digita porto.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS porto (
    codigo        TEXT PRIMARY KEY,             -- ALUMAR, JURUTI, ...
    nome          TEXT NOT NULL,
    un_locode     TEXT,                         -- a confirmar com a operacao
    uf            TEXT,
    pais          TEXT NOT NULL DEFAULT 'BR',
    offset_padrao TEXT NOT NULL DEFAULT '-03:00',
    ativo         INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    observacao    TEXT,
    cor           TEXT                          -- fundo do porto nas telas (#RRGGBB)
);

-- "GUAMARE OIL TERMINAL", "ITQ", "IQI" -> o mesmo porto. Resolucao por JOIN,
-- nunca por palpite. E a mesma ideia do alias_embarcacao do Navios_Timeline.
CREATE TABLE IF NOT EXISTS porto_alias (
    alias        TEXT PRIMARY KEY,              -- MAIUSCULAS, sem acento
    codigo_porto TEXT NOT NULL REFERENCES porto(codigo)
);

-- ---------------------------------------------------------------------------
-- marco_exigido — quais marcos cada tipo de escala pede.
--
-- Tabela, e nao constante no codigo, porque e isto que valida o lancamento e
-- alimenta a fila de escalas incompletas. Cobrar `berth` de quem nao atracou e
-- pedir dado que nao existe: o comandante inventa ou desiste.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marco_exigido (
    tipo_escala TEXT NOT NULL CHECK (tipo_escala IN ('operacional', 'fundeio', 'passagem', 'abertura', 'encerramento')),
    tipo_evento TEXT NOT NULL CHECK (tipo_evento IN ('arrival', 'berth', 'unberth', 'sailing')),
    ordem       INTEGER NOT NULL,               -- ordem cronologica obrigatoria
    PRIMARY KEY (tipo_escala, tipo_evento)
);

-- ---------------------------------------------------------------------------
-- rota_modelo / rota_etapa — o circuito padrao.
--
-- Ao abrir a viagem o sistema ja cria as escalas do modelo, vazias e na ordem.
-- O comandante so preenche horario. O eventual (bunker em Icoaraci ou Itaqui)
-- entra como escala `origem = extra`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rota_modelo (
    id        INTEGER PRIMARY KEY,
    nome      TEXT NOT NULL UNIQUE,
    descricao TEXT,
    ativo     INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE TABLE IF NOT EXISTS rota_etapa (
    rota_modelo_id INTEGER NOT NULL REFERENCES rota_modelo(id),
    ordem          INTEGER NOT NULL,
    codigo_porto   TEXT NOT NULL REFERENCES porto(codigo),
    tipo_escala    TEXT NOT NULL CHECK (tipo_escala IN ('operacional', 'fundeio', 'passagem', 'abertura', 'encerramento')),
    sentido        TEXT NOT NULL DEFAULT 'na' CHECK (sentido IN ('subida', 'descida', 'na')),
    motivo         TEXT NOT NULL,
    condicao       TEXT,                 -- ballast | loading | laden | discharging | ...
    observacao     TEXT,
    PRIMARY KEY (rota_modelo_id, ordem)
);

-- ---------------------------------------------------------------------------
-- viagem — o ciclo Alumar -> Juruti -> Alumar.
--
-- A viagem e definida por DOIS EVENTOS-ANCORA, nao por intervalo de datas:
--   abertura     = o `sailing` da escala de Alumar da viagem ANTERIOR
--   encerramento = o `unberth` da escala de Alumar DESTA viagem
--
-- Consequencia: a escala de Alumar e gravada UMA VEZ SO e serve a duas viagens
-- — seus arrival/berth/unberth fecham esta, seu sailing abre a proxima.
-- Na primeira viagem de um navio nao ha de onde herdar: cria-se uma escala de
-- abertura (`origem = 'abertura'`), que so pede o `sailing`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS viagem (
    id                     INTEGER PRIMARY KEY,
    navio_id               INTEGER NOT NULL REFERENCES navio(id),
    numero                 TEXT NOT NULL,
    rota_modelo_id         INTEGER REFERENCES rota_modelo(id),
    status                 TEXT NOT NULL DEFAULT 'aberta'
                           CHECK (status IN ('aberta', 'encerrada', 'cancelada')),
    meta_observacoes       INTEGER NOT NULL DEFAULT 15,
    evento_abertura_id     INTEGER REFERENCES evento(id),
    evento_encerramento_id INTEGER REFERENCES evento(id),
    aberta_por             TEXT,
    aberta_em              TEXT NOT NULL,
    observacao             TEXT,
    UNIQUE (navio_id, numero)
);

CREATE INDEX IF NOT EXISTS ix_viagem_navio ON viagem (navio_id, status);

-- Um navio so pode ter UMA viagem aberta por vez.
CREATE UNIQUE INDEX IF NOT EXISTS uq_viagem_aberta
    ON viagem (navio_id) WHERE status = 'aberta';

-- ---------------------------------------------------------------------------
-- escala — uma parada da viagem. A unidade de trabalho do comandante.
--
-- A chave e (viagem, ordem) e NUNCA (viagem, porto): Fazendinha aparece duas
-- vezes na mesma viagem, na subida e na descida. `sentido` e o que permite a
-- analise separar as duas.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS escala (
    id           INTEGER PRIMARY KEY,
    viagem_id    INTEGER NOT NULL REFERENCES viagem(id),
    ordem        INTEGER NOT NULL,
    codigo_porto TEXT NOT NULL REFERENCES porto(codigo),
    tipo_escala  TEXT NOT NULL CHECK (tipo_escala IN ('operacional', 'fundeio', 'passagem', 'abertura', 'encerramento')),
    sentido      TEXT NOT NULL DEFAULT 'na' CHECK (sentido IN ('subida', 'descida', 'na')),
    motivo       TEXT NOT NULL CHECK (motivo IN ('carregamento', 'descarga', 'espera_mare',
                                                 'bunker', 'docagem', 'passagem',
                                                 'abertura', 'outro')),
    origem       TEXT NOT NULL CHECK (origem IN ('modelo', 'extra', 'abertura')),
    -- O que o navio esta FAZENDO. Vocabulario do MOTOR_FRETE (aba T_ESCALAS),
    -- para o dado do comandante falar a mesma lingua do motor de viagem.
    -- Nao confundir com `motivo`, que diz POR QUE parou aqui: em Barra Norte a
    -- condicao e `laden` e o motivo e `espera_mare`.
    condicao     TEXT,
    status       TEXT NOT NULL DEFAULT 'aberta'
                 CHECK (status IN ('aberta', 'encerrada', 'cancelada')),
    criada_por   TEXT,
    criada_em    TEXT NOT NULL,
    observacao   TEXT,
    UNIQUE (viagem_id, ordem)
);

CREATE INDEX IF NOT EXISTS ix_escala_viagem ON escala (viagem_id, ordem);
CREATE INDEX IF NOT EXISTS ix_escala_porto ON escala (codigo_porto);

-- ---------------------------------------------------------------------------
-- evento — o marco. Arrival, Berth, Unberth, Sailing.
--
-- Correcao gera VERSAO, nunca sobrescrita: se o UPDATE apagasse o valor
-- anterior, sumiria justamente o que importa numa contestacao — que houve
-- correcao, quando e por quem. `vigente` marca a versao valida; a leitura do
-- dia a dia sai da view evento_vigente.
--
-- `id_cliente` e gerado no celular do comandante. E o que torna o reenvio da
-- fila local inofensivo: tocar duas vezes nao cria evento duplicado.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evento (
   id                  INTEGER PRIMARY KEY,
   id_cliente          TEXT NOT NULL UNIQUE,
   escala_id           INTEGER NOT NULL REFERENCES escala(id),
   tipo                TEXT NOT NULL CHECK (tipo IN ('arrival', 'berth', 'unberth', 'sailing')),

   hora_local          TEXT,
   offset_utc          TEXT,
   hora_utc            TEXT,
   precisao            TEXT NOT NULL DEFAULT 'exata'
                       CHECK (precisao IN ('exata', 'periodo_am', 'periodo_pm',
                                           'apenas_data', 'tbc')),

   -- Combustivel e suprimentos a bordo NO MOMENTO deste marco
   rob_vlsfo           REAL,
   rob_mgo             REAL,
   fw                  REAL,                   -- Agua doce (MT)
   lixo                TEXT,                   -- Solido, Liquido ou N/A
   comentarios         TEXT,                   -- Ocorrencias da escala

   registrado_por      TEXT NOT NULL,
   registrado_em       TEXT NOT NULL,
   nome_responsavel    TEXT NOT NULL,
   observacao          TEXT,

   versao              INTEGER NOT NULL DEFAULT 1,
   vigente             INTEGER NOT NULL DEFAULT 1 CHECK (vigente IN (0, 1)),
   substitui_evento_id INTEGER REFERENCES evento(id),
   motivo_correcao     TEXT,

   CHECK (versao = 1 OR motivo_correcao IS NOT NULL),
   CHECK (precisao = 'tbc' OR hora_local IS NOT NULL),
   CHECK (hora_local IS NULL OR (offset_utc IS NOT NULL AND hora_utc IS NOT NULL))
);

-- um unico marco vigente de cada tipo por escala
CREATE UNIQUE INDEX IF NOT EXISTS uq_evento_vigente
    ON evento (escala_id, tipo) WHERE vigente = 1;
CREATE INDEX IF NOT EXISTS ix_evento_escala ON evento (escala_id, tipo, versao);
CREATE INDEX IF NOT EXISTS ix_evento_utc ON evento (hora_utc);

-- ---------------------------------------------------------------------------
-- abastecimento: quanto entrou de combustivel numa escala de bunker.
--
-- Sem isto o consumo nao fecha: se o navio tinha 100 t e amanhece com 600, a
-- diferenca so faz sentido sabendo quanto foi abastecido no meio.
-- Um por escala; reabastecer a mesma escala substitui o valor.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS abastecimento (
    escala_id        INTEGER PRIMARY KEY REFERENCES escala(id),
    vlsfo            REAL,                   -- toneladas recebidas
    mgo              REAL,
    registrado_por   TEXT NOT NULL,
    registrado_em    TEXT NOT NULL,
    nome_responsavel TEXT NOT NULL,
    observacao       TEXT,
    CHECK (vlsfo IS NOT NULL OR mgo IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- movimento_carga: quanto de bauxita entrou ou saiu nesta escala, em MT.
--
-- Um movimento por escala. A escala de `loading` so carrega; a de `discharging`
-- so descarrega — a validacao vive no servico, olhando a condicao da escala.
--
-- O SALDO a bordo NAO fica aqui: e derivado, na view `carga_bordo`. Guardar o
-- saldo permitiria que ele discordasse dos movimentos que o geraram, e nao
-- haveria como saber qual dos dois esta certo.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS movimento_carga (
    escala_id        INTEGER PRIMARY KEY REFERENCES escala(id),
    carregado        REAL,                   -- MT embarcadas
    descarregado     REAL,                   -- MT desembarcadas
    registrado_por   TEXT NOT NULL,
    registrado_em    TEXT NOT NULL,
    nome_responsavel TEXT NOT NULL,
    observacao       TEXT,
    CHECK (carregado IS NOT NULL OR descarregado IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- conta / conferencia / log_acesso
--
-- A conta e POR NAVIO, nao por pessoa — decisao operacional, por causa da troca
-- de tripulacao. O rastro individual volta pelo campo nome_responsavel do
-- evento, que e obrigatorio.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conta (
    login         TEXT PRIMARY KEY,
    nome_exibicao TEXT NOT NULL,
    perfil        TEXT NOT NULL CHECK (perfil IN ('navio', 'supervisor', 'analytics', 'admin')),
    senha_hash    TEXT NOT NULL,
    navio_id      INTEGER REFERENCES navio(id),  -- obrigatorio no perfil 'navio'
    ativo         INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    criada_em     TEXT NOT NULL,
    CHECK (perfil <> 'navio' OR navio_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS conferencia (
    id            INTEGER PRIMARY KEY,
    evento_id     INTEGER NOT NULL REFERENCES evento(id),
    conferido_por TEXT NOT NULL REFERENCES conta(login),
    conferido_em  TEXT NOT NULL,
    resultado     TEXT NOT NULL CHECK (resultado IN ('conferido', 'contestado')),
    comentario    TEXT
);

CREATE INDEX IF NOT EXISTS ix_conferencia_evento ON conferencia (evento_id);

CREATE TABLE IF NOT EXISTS log_acesso (
    id     INTEGER PRIMARY KEY,
    conta  TEXT,
    acao   TEXT NOT NULL,
    ip     TEXT,
    quando TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_log_quando ON log_acesso (quando);

-- Manter em sincronia com VERSAO_SCHEMA em db.py.
-- ---------------------------------------------------------------------------
-- premissa_pernada — o orcamento de horas de cada pernada.
--
-- Cadastrado pelo admin, uma linha por pernada (as chaves vivem em
-- app/pernadas.py). Onde nao ha linha, a tela de Viagens usa a MEDIA das
-- viagens encerradas do navio. Tabela nova: nao precisa de migracao.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS premissa_pernada (
    chave          TEXT PRIMARY KEY,
    horas          REAL NOT NULL CHECK (horas >= 0),
    atualizado_por TEXT,
    atualizado_em  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- relatorio_salvo — uma combinacao da tela de Analises guardada com nome.
--
-- `consulta` e a query string da tela (base, linhas, colunas, valor, filtros):
-- reabrir e so montar o endereco. Tabela nova: nao precisa de migracao.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relatorio_salvo (
    id         INTEGER PRIMARY KEY,
    nome       TEXT NOT NULL,
    consulta   TEXT NOT NULL,
    criado_por TEXT,
    criado_em  TEXT NOT NULL
);

PRAGMA user_version = 1;
