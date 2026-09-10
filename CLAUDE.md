# Posidonia Ship Ops — banco de dados operacional

> Lido automaticamente ao abrir esta pasta. Contém o **porquê** de cada decisão.
> O plano completo da Fase 1 está em
> `~/.claude/plans/claude-eu-gostaria-de-enchanted-fairy.md`.

---

## O que é

O começo do **banco de dados operacional da Posidonia**. Registra **quando cada coisa
aconteceu e em qual porto** — Arrival, Berth, Unberth e Sailing — capturado **na fonte**, pelo
comandante do navio.

Hoje nenhum sistema da Posidonia faz isso de forma estruturada: o que existe é o e-mail diário
de operações, em texto livre. Tempo de porto, espera de berço, espera de maré e off-hire são
reconstituídos à mão a partir de e-mail.

**Escopo:** os 4 navios **Amazon** (Pathfinder, Pioneer, Commander, Courage), Ship Management da
**Alcoa**, circuito de bauxita **Alumar ⇄ Juruti**.

**Quem usa:** comandante lança (login **por navio**, no **celular**), equipe Posidonia
supervisiona, equipe de analytics consome.

> IMOS/Veson é da área de **Afretamento** e **não entra aqui**. Não puxe contexto de afretamento,
> Time Charter, CT-e ou apuração de EBN Fee para este projeto.

---

## O circuito — a coisa que não dá para adivinhar

A viagem é um ciclo que **abre saindo de Alumar** e **fecha descarregando em Alumar**:

| ordem | porto | atraca? | marcos | o que é |
|---|---|---|---|---|
| 10 | Fazendinha | não | A · S | passagem, **subida** — também chamada **Macapá** |
| 20 | Juruti | **sim** | A · B · U · S | carrega bauxita |
| 30 | Fazendinha | não | A · S | passagem, **descida** |
| 40 | Barra Norte | não | A · S | **espera de maré** |
| 50 | Alumar | **sim** | A · B · U · S | descarrega |

Eventuais, que entram como `origem = 'extra'`: **Icoaraci** (entrando por Mosqueiro) para bunker,
e **Itaqui** para bunker fundeado.

### As quatro regras que sustentam o modelo

**1. A escala de Alumar serve a duas viagens, mas é gravada uma vez só.**
Seus `arrival`/`berth`/`unberth` **fecham** a viagem; seu `sailing` **abre** a seguinte. Por isso
a viagem é definida por **dois eventos-âncora** (`evento_abertura_id`, `evento_encerramento_id`)
e **não** por intervalo de datas.

Consequência prática: a rota-modelo tem **5 etapas, não 6**. Listar o `sailing` de abertura como
sexta etapa criaria a mesma escala física duas vezes. Na **primeira** viagem de um navio não há
de onde herdar — aí, e só aí, cria-se uma escala `origem = 'abertura'`, `tipo_escala = 'abertura'`,
que pede apenas o `sailing`.

**2. A chave da escala é `(viagem, ordem)` — nunca `(viagem, porto)`.**
Fazendinha aparece **duas vezes na mesma viagem**. O campo `sentido` (subida/descida) é o que
permite a análise separar as duas.

**3. O tipo da escala decide quais marcos existem.**
Cobrar `berth` de uma escala de passagem é pedir dado que não existe — o comandante inventa ou
desiste. A tabela `marco_exigido` é a fonte disso, e é ela que alimenta a fila
`escalas_incompletas`.

**4. O ciclo se encadeia sozinho.**
Ao lançar o `sailing` da escala de Alumar (`origem = 'modelo'`), `encadear_ciclo()` fecha a viagem
e abre a seguinte com as 5 escalas vazias. **O comandante nunca pensa em "viagem"** — ele só vê
a próxima parada esperando horário.

O encadeamento é **silencioso de propósito**: o marco em si foi gravado, e devolver o tropeço do
encadeamento como erro faria o comandante achar que perdeu o lançamento. Se faltar o `unberth`, a
viagem simplesmente não fecha e a pendência aparece em `escalas_incompletas` — que é onde ela
tem de aparecer.

**Macapá é a mesma parada que Fazendinha** (confirmado 10/set/2026): **um** porto, com as duas
grafias em `porto_alias`. Cadastrar dois duplicaria a escala.

---

## Regras invioláveis

- **Correção gera versão, nunca sobrescrita.** `vigente = 0` na anterior, `versao + 1` na nova,
  `substitui_evento_id` apontando para trás e **`motivo_correcao` obrigatório**. Se o `UPDATE`
  apagasse o valor antigo, sumiria o que mais importa numa contestação: que houve correção,
  quando e por quem.
- **Fuso é exigido, não inferido.** Diferente do pipeline de e-mail (onde `LT` é ambíguo e a regra
  certa é ser conservador), aqui **o comandante sabe** — ele está lá. Grava-se `hora_local` +
  `offset_utc` + `hora_utc`. **Toda medida de duração sai da `hora_utc`.**
- **`nome_responsavel` é obrigatório.** A conta é por navio; este campo é o que devolve a
  rastreabilidade individual quando o dado virar disputa.
- **`id_cliente` vem do celular e é único.** Reenvio do mesmo id devolve o mesmo evento sem erro —
  é o que torna a fila local inofensiva quando o servidor demora a acordar.
- **Supervisão não bloqueia.** O lançamento entra na hora e já é visível. Conferir vem depois.
- **Enums no banco, não só no Python.** `CHECK` em tudo. O Navios_Timeline deixou os enums apenas
  no Pydantic; com formulário web gravando, isso não basta.
- **Não inventar identificador.** `navio.imo` e `porto.un_locode` ficam **NULOS** até a operação
  confirmar. Número inventado parece certo, e por isso é pior que número ausente.
- **Mudou `schema.sql`?** Incremente `VERSAO_SCHEMA` em `db.py` **e** o `PRAGMA user_version`.

---

## Mapa do código

| Arquivo | O que é |
|---|---|
| `app/schema.sql` | 12 tabelas + 6 views. A fonte da verdade da estrutura |
| `app/seed.sql` | 4 navios, 6 portos, `marco_exigido`, a rota-modelo. `INSERT OR IGNORE`, nunca destrutivo |
| `app/db.py` | Conexão (SQLite local ⇄ Turso), `inicializar`, `agora()`, retry **só de leitura** |
| `app/dominio.py` | Regras puras: `para_utc`, `erros_marco`. Sem banco, sem HTTP |
| `app/viagens.py` | Serviço: `abrir_viagem`, `adicionar_escala_extra`, `lancar_marco`, `encerrar_viagem`, `encadear_ciclo` |
| `app/config.py` | Env vars. `SHIPOPS_SECRET_KEY` é **obrigatória** |
| `scripts/demo_viagem.py` | Duas viagens completas em memória. Rode para ver o modelo funcionando |

**Convenção de retorno:** todo serviço devolve `(resultado, erros)`, com a lista de erros vazia em
caso de sucesso — e devolve **todos** os erros de uma vez, não o primeiro. Exceção aqui é falha de
programação, nunca lançamento inválido do comandante.

---

## Como rodar

```bash
.venv/Scripts/python -m pytest -q          # 42 testes
.venv/Scripts/python scripts/demo_viagem.py
```

O banco fica em `%LOCALAPPDATA%\PosidoniaShipOps\shipops.db` — **fora do OneDrive**, que corrompe
arquivo aberto e cujo caminho longo estoura o `MAX_PATH` do Windows no `pip install`.

---

## Estado e próximos passos

**Fase 1 — pronta.** Schema, seed, rota-modelo, escalas extras, lançamento, correção versionada,
encerramento por evento-âncora, views de duração e as duas filas (pendências e conferência).

**A fazer, na ordem:**
2. **Fase 2** — Auth (senha por conta, `hashlib.scrypt`, cookie HMAC) + telas do comandante em Jinja2 + **fila
   local (IndexedDB)** — é ela que esconde o cold start do Render e faz o sistema ser usado.
3. Painel e filas da supervisão.
4. Deploy: Docker no Render (free) + **Turso desde o primeiro deploy** + ping externo em
   `/api/health`.
5. Views de análise + export CSV com token na URL (o Excel não faz login por tela).
6. Piloto: **um** navio, 2 viagens completas.

---

## O que "Arrival" significa aqui — resolvido em 10/set/2026

**Não há convenção de fundeadouro, barra ou estação de prático.** Arrival é a **hora de chegada
oficial**, como o comandante a reporta. É assim que a operação já trabalha, e forçar uma
definição mais estrita só faria o comandante preencher errado com cara de certo.

Consequência a carregar com honestidade: a **espera de berço** (`berth − arrival`) herda a
variação de critério entre comandantes. Serve para ver tendência e ordem de grandeza; não serve,
sozinha, como número contratual. Se um dia precisar dessa precisão, o caminho é acrescentar o
marco **NOR**, que tem definição formal — não apertar a regra do Arrival.

**Icoaraci: fundeia, não atraca** (confirmado 10/set/2026). Escala de `tipo_escala = 'fundeio'`.

---

## Pendências com a operação

1. **Mosqueiro é escala ou só o canal de entrada de Icoaraci?**
2. **Alumar e Itaqui são portos distintos?** São terminais diferentes em São Luís.
3. **Que número de viagem** o comandante conhece e sabe informar? Hoje é gerado (`AAAA-NNN`).
4. **Juruti e Alumar têm fundeio de espera** antes de atracar — escala separada ou embutido no
   intervalo `arrival → berth`?
5. **IMO dos 4 navios** e **UN/LOCODE dos 6 portos**, para preencher os campos deixados nulos.
