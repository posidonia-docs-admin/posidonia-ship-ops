-- Posidonia Ship Ops — views.
--
-- Arquivo separado de proposito: as views leem colunas que a MIGRACAO cria, e
-- schema.sql roda ANTES dela. Criar view sobre coluna inexistente derruba o
-- arranque inteiro num banco que ja existe — como o de producao. A ordem e:
--     schema.sql (tabelas) -> _migrar (colunas) -> views.sql -> seed.sql
-- Idempotente: todo CREATE VIEW e precedido de DROP VIEW IF EXISTS.

-- ===========================================================================
-- VIEWS
-- ===========================================================================

DROP VIEW IF EXISTS evento_vigente;
CREATE VIEW evento_vigente AS
SELECT * FROM evento WHERE vigente = 1;

-- Os quatro marcos de cada escala em colunas.
DROP VIEW IF EXISTS escala_marcos;
CREATE VIEW escala_marcos AS
SELECT
    e.id            AS escala_id,
    e.viagem_id,
    e.ordem,
    e.codigo_porto,
    p.nome          AS porto_nome,
    e.tipo_escala,
    e.sentido,
    e.motivo,
    e.origem,
    e.status,
    MAX(CASE WHEN v.tipo = 'arrival' THEN v.hora_utc END) AS arrival_utc,
    MAX(CASE WHEN v.tipo = 'berth'   THEN v.hora_utc END) AS berth_utc,
    MAX(CASE WHEN v.tipo = 'unberth' THEN v.hora_utc END) AS unberth_utc,
    MAX(CASE WHEN v.tipo = 'sailing' THEN v.hora_utc END) AS sailing_utc,
    COUNT(v.id)                                           AS marcos_lancados
FROM escala e
JOIN porto p ON p.codigo = e.codigo_porto
LEFT JOIN evento_vigente v ON v.escala_id = e.id
GROUP BY e.id;

-- As duracoes que hoje ninguem tem.
DROP VIEW IF EXISTS escala_completa;
CREATE VIEW escala_completa AS
SELECT
    m.*,
    ROUND((julianday(m.berth_utc)   - julianday(m.arrival_utc)) * 24, 2) AS horas_espera_berco,
    ROUND((julianday(m.unberth_utc) - julianday(m.berth_utc))   * 24, 2) AS horas_atracado,
    ROUND((julianday(m.sailing_utc) - julianday(m.unberth_utc)) * 24, 2) AS horas_pos_operacao,
    ROUND((julianday(m.sailing_utc) - julianday(m.arrival_utc)) * 24, 2) AS horas_total_escala
FROM escala_marcos m;

-- Fila de cobranca: marco exigido pelo tipo da escala que ainda nao foi lancado.
DROP VIEW IF EXISTS escalas_incompletas;
CREATE VIEW escalas_incompletas AS
SELECT
    e.id           AS escala_id,
    e.viagem_id,
    e.ordem,
    e.codigo_porto,
    e.tipo_escala,
    me.tipo_evento AS marco_faltante,
    me.ordem       AS marco_ordem
FROM escala e
JOIN marco_exigido me ON me.tipo_escala = e.tipo_escala
LEFT JOIN evento_vigente v ON v.escala_id = e.id AND v.tipo = me.tipo_evento
WHERE e.status <> 'cancelada'
  AND v.id IS NULL
ORDER BY e.viagem_id, e.ordem, me.ordem;

-- Fila da supervisao: marco lancado que ainda nao passou por conferencia.
DROP VIEW IF EXISTS escalas_a_conferir;
CREATE VIEW escalas_a_conferir AS
SELECT
    v.id AS evento_id, v.escala_id, e.viagem_id, e.codigo_porto, e.ordem,
    v.tipo, v.hora_local, v.offset_utc, v.hora_utc,
    v.nome_responsavel, v.registrado_por, v.registrado_em, v.versao
FROM evento_vigente v
JOIN escala e ON e.id = v.escala_id
LEFT JOIN conferencia c ON c.evento_id = v.id
WHERE c.id IS NULL
ORDER BY v.registrado_em;

-- A viagem inteira num relance.
DROP VIEW IF EXISTS viagem_completa;
CREATE VIEW viagem_completa AS
SELECT
    vg.id AS viagem_id,
    vg.numero,
    n.nome_oficial AS navio,
    vg.status,
    ab.hora_utc AS abertura_utc,
    en.hora_utc AS encerramento_utc,
    ROUND((julianday(en.hora_utc) - julianday(ab.hora_utc)) * 24, 2) AS horas_viagem,
    (SELECT COUNT(*) FROM escala e WHERE e.viagem_id = vg.id)                        AS escalas,
    (SELECT COUNT(*) FROM escala e WHERE e.viagem_id = vg.id AND e.origem = 'extra') AS escalas_extras,
    (SELECT COUNT(*) FROM escalas_incompletas i WHERE i.viagem_id = vg.id)           AS marcos_faltantes
FROM viagem vg
JOIN navio n ON n.id = vg.navio_id
LEFT JOIN evento ab ON ab.id = vg.evento_abertura_id
LEFT JOIN evento en ON en.id = vg.evento_encerramento_id;

-- ---------------------------------------------------------------------------
-- COMBUSTIVEL
--
-- Regra do estoque, e ela decide se a conta fecha:
--   ROB e SEMPRE o que esta a bordo NAQUELE instante, ja contando o que acabou
--   de receber. Por isso o abastecimento de uma escala entra no ULTIMO marco
--   dela (a saida) — quando o navio deixa o porto o combustivel ja esta dentro.
--
--   consumo(anterior -> atual) = ROB_anterior + abastecido_atual - ROB_atual
--
-- Se um dia o comandante passar a reportar o ROB ANTES de abastecer, esta conta
-- passa a contar o bunker duas vezes. E a premissa a vigiar.
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS combustivel_bordo;
CREATE VIEW combustivel_bordo AS
SELECT
    vg.navio_id,
    n.nome_oficial   AS navio,
    vg.numero        AS viagem,
    e.id             AS escala_id,
    e.ordem,
    e.codigo_porto,
    p.nome           AS porto,
    e.condicao,
    e.motivo,
    ev.id            AS evento_id,
    ev.tipo          AS marco,
    ev.hora_local,
    ev.hora_utc,
    ev.rob_vlsfo,
    ev.rob_mgo,
    CASE WHEN ev.hora_utc = (SELECT MAX(x.hora_utc) FROM evento_vigente x
                              WHERE x.escala_id = e.id)
         THEN ab.vlsfo END AS abastecido_vlsfo,
    CASE WHEN ev.hora_utc = (SELECT MAX(x.hora_utc) FROM evento_vigente x
                              WHERE x.escala_id = e.id)
         THEN ab.mgo END   AS abastecido_mgo,
    ev.nome_responsavel
FROM evento_vigente ev
JOIN escala e   ON e.id = ev.escala_id
JOIN viagem vg  ON vg.id = e.viagem_id
JOIN navio n    ON n.id = vg.navio_id
JOIN porto p    ON p.codigo = e.codigo_porto
LEFT JOIN abastecimento ab ON ab.escala_id = e.id
WHERE ev.rob_vlsfo IS NOT NULL
   OR ev.rob_mgo IS NOT NULL
   OR ab.escala_id IS NOT NULL;

DROP VIEW IF EXISTS _leituras_combustivel;
CREATE VIEW _leituras_combustivel AS
SELECT b.*,
       LAG(b.rob_vlsfo) OVER (PARTITION BY b.navio_id ORDER BY b.hora_utc)
           AS rob_vlsfo_anterior,
       LAG(b.rob_mgo) OVER (PARTITION BY b.navio_id ORDER BY b.hora_utc)
           AS rob_mgo_anterior,
       LAG(b.hora_utc) OVER (PARTITION BY b.navio_id ORDER BY b.hora_utc)
           AS hora_anterior
FROM combustivel_bordo b;

DROP VIEW IF EXISTS consumo_combustivel;
CREATE VIEW consumo_combustivel AS
SELECT l.*,
       ROUND(l.rob_vlsfo_anterior + COALESCE(l.abastecido_vlsfo, 0) - l.rob_vlsfo, 3)
           AS consumo_vlsfo,
       ROUND(l.rob_mgo_anterior + COALESCE(l.abastecido_mgo, 0) - l.rob_mgo, 3)
           AS consumo_mgo,
       ROUND((julianday(l.hora_utc) - julianday(l.hora_anterior)) * 24, 2)
           AS horas_desde_a_leitura_anterior
FROM _leituras_combustivel l;

-- ---------------------------------------------------------------------------
-- CARGA A BORDO
--
-- O saldo e ACUMULADO por navio e ATRAVESSA viagens: carrega 58.000, descarrega
-- 57.500, ficam 500 a bordo; na viagem seguinte carrega 58.000 (saldo 58.500) e
-- descarrega 58.000 (volta a 500). Uma hora ele descarrega acima do carregado e
-- zera a sobra.
--
-- Derivado, nunca guardado: um saldo gravado poderia discordar dos movimentos
-- que o geraram, e nao haveria como saber qual dos dois esta certo.
--
-- A ordem e o instante do ULTIMO marco da escala — quando a operacao terminou.
-- Escala sem marco ainda entra com `momento` nulo e ordena pelo id, para o
-- movimento nao sumir da conta so por falta de horario.
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS carga_bordo;
CREATE VIEW carga_bordo AS
WITH movimentos AS (
    SELECT
        vg.navio_id,
        n.nome_oficial AS navio,
        vg.numero      AS viagem,
        e.id           AS escala_id,
        e.ordem,
        e.codigo_porto,
        p.nome         AS porto,
        e.condicao,
        COALESCE(mc.carregado, 0)    AS carregado,
        COALESCE(mc.descarregado, 0) AS descarregado,
        mc.nome_responsavel,
        (SELECT MAX(x.hora_utc) FROM evento_vigente x WHERE x.escala_id = e.id)
            AS momento
    FROM movimento_carga mc
    JOIN escala e  ON e.id = mc.escala_id
    JOIN viagem vg ON vg.id = e.viagem_id
    JOIN navio n   ON n.id = vg.navio_id
    JOIN porto p   ON p.codigo = e.codigo_porto
)
SELECT m.*,
       ROUND(SUM(m.carregado - m.descarregado) OVER (
                 PARTITION BY m.navio_id
                 ORDER BY m.momento, m.escala_id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 3)
           AS carga_bordo
FROM movimentos m;
