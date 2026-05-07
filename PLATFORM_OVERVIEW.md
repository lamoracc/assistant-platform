# Assistant Platform Overview

## Назначение платформы

`assistant-platform` задуман как внутренняя платформа ассистента для выполнения
рутинных задач в компании. Текущая реализация сфокусирована на RAG-поиске и
ответах по загруженной документации, но это первый слой более широкой системы.

Целевое направление развития:

- отвечать на вопросы по внутренним продуктам, регламентам и базе знаний;
- помогать сотрудникам быстро находить инструкции, процедуры и связанные
  документы;
- готовить сводки и черновики по рабочим источникам, например почте,
  задачам, чатам и документам;
- выполнять повторяемые операции через безопасные интеграции с внутренними
  сервисами;
- хранить контекст диалогов, источников и действий с учетом прав доступа.

Текущий датасет с документацией OPERA используется как первый практический
пример большой базы знаний. Архитектура не должна оставаться привязанной к
этому домену: ingestion, retrieval, ассистенты и future integrations должны
работать с разными источниками и продуктами компании.

## Что уже реализовано

### API

Основной backend находится в `apps/api` и построен на FastAPI.

Ключевые endpoints:

- `GET /health` - проверка доступности API.
- `POST /documents/upload` - загрузка одного документа.
- `POST /documents/import-folder` - рекурсивный импорт папки документов.
- `POST /chat/query` - вопрос к проиндексированной базе знаний.
- `POST /debug/search` - диагностический поиск.
- `GET /debug/qdrant` - состояние Qdrant collection.

### Ingestion

Платформа принимает разные форматы документов:

- HTML / HTM
- PDF
- DOC / DOCX
- Markdown
- TXT

Pipeline ingestion:

1. Определяет тип файла.
2. Извлекает текст и metadata.
3. Очищает навигацию, пустые блоки, image-only строки и служебный шум.
4. Делит текст на chunks.
5. Классифицирует chunks как `content`, `procedure`, `table`, `reference`,
   `navigation` или `empty`.
6. Пропускает `navigation` и `empty`.
7. Считает embeddings.
8. Сохраняет документы и chunks в PostgreSQL.
9. Сохраняет vectors в Qdrant.

Для массового импорта добавлен batch ingestion: chunks копятся из нескольких
документов и отправляются в embedding model пачками. Это особенно важно для
больших наборов маленьких файлов.

### Retrieval

Поиск сейчас гибридный:

- vector search в Qdrant;
- keyword fallback через PostgreSQL;
- generic ranking по совпадению фразы, query terms, заголовков, длины chunk и
  типу chunk;
- exact duplicate dedupe по нормализованному content fingerprint;
- near-duplicate grouping по normalized body similarity;
- debug diagnostics для объяснения filtered results.

Deduplication выполняется только на этапе retrieval. Документы и vectors из
индекса не удаляются.

### LLM Provider

Если `LLM_PROVIDER_URL` не задан, `/chat/query` возвращает retrieval-only ответ
с найденными источниками. Если задан OpenAI-compatible endpoint, API строит
prompt и отправляет запрос в настроенную модель.

### Хранилища

- PostgreSQL хранит документы, chunks, metadata, links и image assets.
- Qdrant хранит vectors chunks.
- Redis пока заложен как foundation для очередей/cache.
- Docker Compose поднимает весь стек локально или на VM.

## Как используется система

### Запуск

```bash
cd /home/lamorac/assistant-platform
docker compose up -d --build
```

Проверка:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/debug/qdrant
```

### Импорт документов

Один файл:

```bash
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@/path/to/document.md"
```

Папка:

```bash
curl -X POST http://localhost:8000/documents/import-folder \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/my_documents"}'
```

### Запрос к базе знаний

```bash
curl -X POST http://localhost:8000/chat/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I configure access permissions?","debug":true}'
```

### Диагностический поиск

```bash
curl -X POST http://localhost:8000/debug/search \
  -H "Content-Type: application/json" \
  -d '{"question":"access permissions configuration"}'
```

## Сильные стороны

- Архитектура уже разделена на API, services, models и инфраструктурные
  сервисы.
- Есть рабочий end-to-end RAG flow: import -> embedding -> storage -> retrieval
  -> answer.
- Поддерживается несколько форматов документов.
- Qdrant и PostgreSQL разделяют vector search и metadata storage.
- Batch ingestion значительно лучше подходит для больших наборов файлов, чем
  документ-за-документом.
- Duplicate и near-duplicate filtering выполняются на retrieval-stage, не
  разрушая индекс.
- Retrieval diagnostics помогают понимать, почему chunks попали или не попали
  в ответ.
- Embedding model и LLM provider конфигурируются через environment variables.
- Есть базовые тесты для extraction, sanitizer, ranking, ingestion batch и
  retrieval deduplication.

## Слабые стороны и риски

- Ingestion пока выполняется синхронно в API request. Для больших импортов это
  хрупко: request может оборваться, а прогресс хранится только в логах.
- Worker service пока placeholder и не выполняет реальную работу.
- Нет отдельной модели `import_jobs`, статусов jobs, checkpoint/resume и UI/API
  для управления импортом.
- Нет миграций БД. Используется `create_all`, что неудобно и рискованно при
  развитии схемы.
- Нет authentication, authorization, ролей и audit trail. Для корпоративного
  ассистента это критично.
- Нет tenant/source isolation: разные продукты, команды и уровни доступа пока
  не разделены явно.
- Prompt и часть документации все еще несут следы первого OPERA use case.
  Логику нужно держать generic, а domain-specific правила выносить в настройки
  source/profile.
- Keyword search основан на `ILIKE`, что может деградировать на больших объемах.
- Reranker interface есть, но настоящий reranker пока не подключен.
- Нет полноценной оценки качества retrieval: golden questions, recall@k,
  source accuracy, regression suite.
- Нет observability: metrics, tracing, structured logs, latency breakdown,
  alerting.
- Нет безопасного tool/action layer для выполнения задач ассистентом во внешних
  системах.
- Нет политики хранения/маскирования конфиденциальных данных.

## Что улучшить в ближайшую очередь

### 1. Перенести ingestion в worker

Нужно сделать импорт фоновой задачей:

- `POST /documents/import-folder` создает import job и сразу возвращает `job_id`;
- worker читает job из Redis/PostgreSQL;
- прогресс пишется в БД;
- API предоставляет endpoints для статуса, отмены и повторного запуска;
- batch commit остается, но становится управляемым и resumable.

### 2. Добавить миграции

Подключить Alembic:

- версионировать schema changes;
- убрать зависимость от `create_all` для production-like окружений;
- безопасно развивать таблицы для ассистентов, источников, jobs и прав доступа.

### 3. Обобщить модель источников знаний

Добавить сущности:

- `knowledge_sources`
- `source_collections`
- `source_type`
- `product`
- `team`
- `visibility`
- `owner`
- `refresh_policy`

Это позволит хранить документацию разных продуктов, внутренних регламентов,
почтовых сводок и других источников в одной платформе без смешивания контекста.

### 4. Ввести security model

Для корпоративного использования нужны:

- пользователи и роли;
- разграничение доступа к sources/chunks;
- audit trail запросов и ответов;
- политика работы с конфиденциальными данными;
- redaction/masking для чувствительных полей;
- запрет на выдачу sources, к которым пользователь не имеет доступа.

### 5. Улучшить retrieval quality

Следующие шаги:

- PostgreSQL full-text search или отдельный search engine;
- cross-encoder/reranker;
- source-aware ranking;
- evaluation set с типовыми вопросами;
- метрики качества и regression tests.

### 6. Развить LLM orchestration

Нужны:

- profiles для разных ассистентов;
- system prompts по задачам;
- conversation memory;
- citation policy;
- guardrails;
- structured outputs;
- fallback strategies, когда источников недостаточно.

### 7. Добавить tool/action layer

Будущий ассистент должен не только отвечать, но и выполнять рутинные задачи.
Для этого нужен слой инструментов:

- декларативное описание tool capabilities;
- permissions per tool;
- dry-run и confirmation для опасных действий;
- audit logs;
- connectors к почте, календарю, задачам, CRM/ERP, внутренним API.

### 8. Улучшить эксплуатацию

Добавить:

- structured logs;
- metrics по ingestion/retrieval/LLM latency;
- health checks для Postgres/Qdrant/Redis/model loading;
- backup/restore procedures;
- CI tests;
- staging/prod profiles.

## Рекомендуемая дорожная карта

### Этап 1. Сделать текущий RAG надежным

- Worker-based ingestion.
- Import jobs и progress API.
- Alembic migrations.
- Source model.
- Evaluation dataset.

### Этап 2. Сделать ассистента корпоративным

- Users/roles/permissions.
- Source-level access control.
- Conversation history.
- Assistant profiles.
- Better reranking and citations.

### Этап 3. Добавить рабочие интеграции

- Email summary pipeline.
- Calendar/task connectors.
- Internal product knowledge sources.
- Tool execution with approvals.
- Audit and compliance layer.

### Этап 4. Подготовить production usage

- Observability.
- Backups.
- CI/CD.
- Separate environments.
- Secrets management.
- Performance tuning.

## Текущее состояние на сервере

Последняя проверенная загрузка `/data/opera_full`:

- files processed: `10768`
- indexed documents: `9346`
- skipped empty/noisy files: `1422`
- failed: `0`
- chunks/vectors: `34511`

PostgreSQL `document_chunks` и Qdrant `points_count` совпадали на `34511`.

Этот датасет стоит рассматривать как первый загруженный knowledge source, а не
как границу продукта.
