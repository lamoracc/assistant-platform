# Assistant Platform: структура проекта и контекст для переноса на VM

Этот документ описывает проект `assistant-platform`, его архитектуру, основные файлы, сервисы, данные и важные нюансы переноса на другую VM, например VM с NVIDIA V100.

## Назначение проекта

Проект представляет собой RAG-платформу для поиска и ответов по документации OPERA PMS.

Основные возможности:

- импорт mixed documentation folders: `html`, `htm`, `pdf`, `doc`, `docx`, `md`, `txt`, `jpg`, `png`, `gif`;
- извлечение текста и metadata из документации;
- очистка навигации, breadcrumbs, printer/email UI, image-only markdown строк;
- chunking по заголовкам, параграфам, спискам и таблицам;
- определение языка документа и chunk;
- классификация chunk:
  - `content`
  - `procedure`
  - `table`
  - `reference`
  - `navigation`
  - `empty`
- пропуск `navigation` и `empty` chunks при индексации;
- multilingual embeddings через `BAAI/bge-m3`;
- хранение metadata в PostgreSQL;
- хранение vectors в Qdrant;
- hybrid retrieval:
  - Qdrant vector search;
  - PostgreSQL keyword search;
  - metadata filters;
  - reranker-ready interface;
- RAG chat endpoint `/chat/query`;
- diagnostic endpoints для extraction, chunks, search и Qdrant.

## Технологический стек

- Python 3.12
- FastAPI
- SQLAlchemy
- PostgreSQL 16
- Qdrant
- Redis
- Docker Compose
- sentence-transformers
- `BAAI/bge-m3` multilingual embedding model
- BeautifulSoup
- pypdf
- python-docx
- Pillow
- langdetect

## Docker Compose сервисы

Файл: `docker-compose.yml`

### `api`

Основной FastAPI backend.

Запускается командой:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Порты:

- host `8000` -> container `8000`

Volumes:

- `./apps/api:/app`
- `./data:/data`

Важно: `/data` внутри контейнера содержит локальные OPERA docs, если они лежат в `./data` на host.

### `worker-ingest`

Пока placeholder worker. Реальный ingestion сейчас выполняется API endpoint-ами.

### `postgres`

PostgreSQL для metadata документов/chunks/assets/links.

Порт:

- `5432`

Volume:

- `postgres_data:/var/lib/postgresql/data`

### `redis`

Redis foundation для будущих queues/cache.

Порт:

- `6379`

Volume:

- `redis_data:/data`

### `qdrant`

Vector database.

Порты:

- `6333` REST
- `6334` gRPC

Volume:

- `qdrant_data:/qdrant/storage`

## Важные переменные окружения

Основные переменные заданы в `docker-compose.yml` и `.env.example`.

```env
APP_ENV=development

DATABASE_URL=postgresql+psycopg://assistant:assistant@postgres:5432/assistant
REDIS_URL=redis://redis:6379/0

QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION_NAME=document_chunks

EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024

UPLOAD_MAX_BYTES=26214400

RETRIEVAL_TOP_K=8
RETRIEVAL_MIN_SCORE=0.55
MAX_CONTEXT_CHARS=12000

LLM_PROVIDER_URL=
LLM_MODEL_NAME=qwen
```

Важно: `BAAI/bge-m3` использует vectors размерности `1024`. Если Qdrant collection была создана старой моделью на `384`, collection/volume нужно пересоздать.

## Дерево проекта

Высокоуровневая структура:

```text
.
├── apps
│   ├── api
│   │   ├── app
│   │   │   ├── api
│   │   │   ├── core
│   │   │   ├── models
│   │   │   ├── services
│   │   │   └── main.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── worker-ingest
│       ├── Dockerfile
│       ├── main.py
│       └── requirements.txt
├── data
│   ├── opera_docs
│   └── opera_md
├── tests
├── docker-compose.yml
├── .env.example
├── README.md
└── PROJECT_HANDOFF.md
```

Локально сейчас есть большие datasets:

- `data/opera_docs`: примерно `958M`, около `15715` файлов;
- `data/opera_md`: примерно `66M`, около `10768` файлов.

Эти данные могут не быть в git. При переносе их нужно отдельно скопировать или заново подготовить на новой VM.

## FastAPI entrypoint

Файл: `apps/api/app/main.py`

Он:

- создает FastAPI app;
- на startup вызывает:
  - `init_db()`;
  - `init_qdrant_collection()`;
- подключает routers:
  - documents;
  - chat;
  - debug.

## API endpoints

### Health

```http
GET /health
```

### Document ingestion

```http
POST /documents/upload
```

Multipart upload одного файла.

Поддерживаемые форматы:

- `.html`
- `.htm`
- `.pdf`
- `.doc`
- `.docx`
- `.md`
- `.txt`

### Folder import

```http
POST /documents/import-folder
```

Пример:

```bash
curl -X POST http://localhost:8000/documents/import-folder \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/opera_md"}'
```

Импортирует папку рекурсивно.

### Chat query

```http
POST /chat/query
```

Пример:

```bash
curl -X POST http://localhost:8000/chat/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Как настроить packages в OPERA PMS?","debug":true}'
```

Если `LLM_PROVIDER_URL` не задан, возвращается retrieval-only answer с sources.

Если `LLM_PROVIDER_URL` задан, используется OpenAI-compatible endpoint:

```text
/v1/chat/completions
```

Подходит для vLLM/Qwen.

### Debug endpoints

```http
GET /debug/qdrant
POST /debug/qdrant/smoke-test
POST /debug/extract-file
POST /debug/chunks
POST /debug/search
```

Примеры:

```bash
curl http://localhost:8000/debug/qdrant

curl -X POST http://localhost:8000/debug/qdrant/smoke-test

curl -X POST http://localhost:8000/debug/extract-file \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/opera_md/package_configuration.md"}'

curl -X POST http://localhost:8000/debug/chunks \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/opera_md/package_configuration.md"}'

curl -X POST http://localhost:8000/debug/search \
  -H "Content-Type: application/json" \
  -d '{"question":"Как настроить packages?","filters":{"chunk_type":"procedure"}}'
```

## Backend modules

### API routers

Directory: `apps/api/app/api`

```text
chat.py
debug.py
documents.py
```

#### `documents.py`

Endpoints:

- `/documents/upload`
- `/documents/import-folder`

Отвечает за upload/import и вызывает ingestion layer.

#### `chat.py`

Endpoint:

- `/chat/query`

Вызывает retrieval, prompt builder и LLM provider.

#### `debug.py`

Diagnostic endpoints:

- Qdrant status;
- Qdrant smoke test;
- extract file;
- chunks preview;
- search diagnostics.

### Core

Directory: `apps/api/app/core`

#### `config.py`

Считывает environment variables.

Ключевые настройки:

- database URL;
- Qdrant URL/collection;
- embedding model/dimensions;
- retrieval thresholds;
- LLM provider URL.

#### `database.py`

SQLAlchemy engine/session.

Сейчас используется:

```python
Base.metadata.create_all(bind=engine)
```

Это удобно для dev, но для production лучше добавить Alembic migrations.

### Models

Directory: `apps/api/app/models`

#### `document.py`

SQLAlchemy models:

- `Document`
- `DocumentChunk`
- `DocumentLink`
- `ImageAsset`

#### `Document`

Хранит:

- filename;
- source_path;
- file_hash;
- content_type;
- source_type;
- status;
- text_length;
- chunk_count;
- doc_metadata JSONB;
- timestamps.

#### `DocumentChunk`

Хранит:

- document_id;
- vector_id;
- chunk_index;
- heading;
- content;
- char_count;
- chunk_metadata JSONB.

Важно: сами vectors не хранятся в PostgreSQL, они лежат в Qdrant.

#### `DocumentLink`

Хранит links между HTML/Markdown/docs страницами.

#### `ImageAsset`

Хранит image assets:

- path;
- filename;
- dimensions;
- content_type;
- referenced_by_document_id;
- metadata.

### Services

Directory: `apps/api/app/services`

```text
chunking.py
document_extractors.py
embeddings.py
file_router.py
ingestion.py
language.py
llm_provider.py
normalization.py
prompt_builder.py
qdrant_store.py
reranker.py
retrieval.py
text_sanitizer.py
```

#### `file_router.py`

Роутит файл по extension и MIME type.

Routes:

- `html`
- `pdf`
- `word`
- `text`
- `image`
- `ignored`
- `unsupported`

Поддерживаемые расширения:

- HTML: `.html`, `.htm`
- PDF: `.pdf`
- Word: `.doc`, `.docx`
- Text: `.md`, `.txt`
- Images: `.jpg`, `.jpeg`, `.png`, `.gif`
- Ignored: `.css`, `.js`

#### `document_extractors.py`

Извлекает text/metadata/blocks.

Поддерживает:

- HTML через BeautifulSoup;
- PDF через pypdf;
- DOCX через python-docx;
- legacy DOC через printable text fallback;
- Markdown;
- plain text.

Markdown extractor:

- парсит YAML front matter;
- достает metadata:
  - topic_id;
  - title;
  - relative_path;
  - section_group;
  - breadcrumbs;
- удаляет YAML front matter из chunk text;
- удаляет image-only lines;
- удаляет breadcrumb-only lines;
- удаляет previous/next navigation tables;
- сохраняет meaningful headings, paragraphs, lists, tables.

#### `text_sanitizer.py`

Функция:

```python
sanitize_text(value)
```

Удаляет:

- NUL bytes;
- invalid control characters;
- лишние пробелы.

Используется до chunking и перед DB/Qdrant insert.

#### `normalization.py`

Содержит:

- `normalize_text`;
- `classify_chunk_type`;
- `enrich_chunk_metadata`.

Классифицирует chunk:

- `content`
- `procedure`
- `table`
- `reference`
- `navigation`
- `empty`

#### `language.py`

Определение языка.

Сначала пытается использовать `langdetect`, затем fallback по Cyrillic/Latin characters.

#### `chunking.py`

Chunking по headings/paragraphs.

Добавляет в chunk metadata:

- language;
- chunk_type.

#### `ingestion.py`

Главный ingestion orchestration.

Основной pipeline:

1. hash file;
2. detect duplicate;
3. extract document;
4. chunk document;
5. skip `navigation`/`empty` chunks;
6. create PostgreSQL `Document`;
7. create PostgreSQL `DocumentChunk`;
8. generate embeddings;
9. upsert Qdrant points;
10. commit PostgreSQL только после успешного Qdrant upsert.

Если Qdrant upsert fails:

- PostgreSQL rollback;
- raises `IngestionError`.

Есть reindex path: если document уже есть по hash, chunks из PostgreSQL повторно upsert-ятся в Qdrant.

#### `embeddings.py`

Использует sentence-transformers.

Default model:

```text
BAAI/bge-m3
```

Embeddings нормализуются:

```python
model.encode(texts, normalize_embeddings=True)
```

#### `qdrant_store.py`

Работа с Qdrant:

- init collection;
- upsert chunks;
- collection status;
- smoke-test point.

Upsert логирует:

- collection name;
- number of points;
- first point id;
- vector dimension;
- Qdrant upsert result.

#### `retrieval.py`

Hybrid retrieval:

- vector search в Qdrant;
- filtering по `RETRIEVAL_MIN_SCORE`;
- PostgreSQL keyword fallback;
- metadata filters;
- merge results;
- reranker-ready hook.

Keyword search ищет по:

- `document_chunks.content`;
- `document_chunks.heading`;
- `documents.filename`;
- `documents.source_path`;
- `documents.doc_metadata["title"]`.

#### `reranker.py`

Сейчас no-op interface.

Можно заменить на реальный reranker позже.

#### `prompt_builder.py`

Строит system prompt и context.

Для русских вопросов:

- ответ должен быть на русском;
- OPERA UI terms/menu/button/field names сохраняются на английском.

#### `llm_provider.py`

Provider abstraction.

Если `LLM_PROVIDER_URL` не задан:

- retrieval-only mode.

Если задан:

- OpenAI-compatible `/v1/chat/completions`.

Подходит для vLLM.

## Tests

Directory: `tests`

```text
test_text_sanitizer.py
test_markdown_extractor.py
```

Запуск:

```bash
python3 -m unittest tests.test_text_sanitizer tests.test_markdown_extractor
```

Проверяют:

- удаление NUL bytes;
- удаление invalid control characters;
- нормализацию whitespace;
- Markdown front matter parsing;
- удаление image-only lines;
- удаление breadcrumbs;
- удаление previous/next navigation tables;
- сохранение meaningful Markdown content.

## Важные команды

### Запуск

```bash
docker compose up --build
```

Фоновый режим:

```bash
docker compose up --build -d
```

### Остановка

```bash
docker compose down
```

### Полный reset volumes

```bash
docker compose down -v
docker compose up --build
```

Нужно делать, если:

- изменилась размерность embeddings;
- Qdrant collection создана со старой размерностью;
- schema PostgreSQL изменилась, а Alembic migrations еще нет.

## Перенос на другую VM

### Что нужно установить на VM

Минимум:

```bash
docker
docker compose
git
```

Если VM с NVIDIA V100 и планируется LLM/vLLM:

```bash
nvidia-driver
nvidia-container-toolkit
```

Проверка GPU:

```bash
nvidia-smi
```

### Вариант 1: перенести только код и заново импортировать данные

Это самый надежный вариант.

1. Скопировать/клонировать проект.
2. Скопировать `data/` или подготовить OPERA docs на VM.
3. Запустить:

```bash
docker compose up --build -d
```

4. Импортировать docs:

```bash
curl -X POST http://localhost:8000/documents/import-folder \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/opera_md"}'
```

или:

```bash
curl -X POST http://localhost:8000/documents/import-folder \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/opera_docs/OperaHelp"}'
```

### Вариант 2: переносить Docker volumes

Нужно переносить:

- `postgres_data`;
- `qdrant_data`;
- возможно `redis_data`, хотя Redis сейчас не критичен.

Минусы:

- можно получить несовместимость Qdrant collection dimension;
- schema создается через `create_all`, migrations пока нет;
- проще ошибиться с named volumes.

Для первого переноса лучше использовать вариант 1.

## Особенности для VM с NVIDIA V100

Сейчас embeddings выполняются внутри API container на CPU, если не настроить GPU runtime.

Можно использовать V100 для:

1. ускорения embeddings `BAAI/bge-m3`;
2. запуска local LLM через vLLM/Qwen.

Для LLM можно поднять отдельный сервис/процесс vLLM:

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --host 0.0.0.0 --port 8001
```

И задать:

```env
LLM_PROVIDER_URL=http://host.docker.internal:8001
LLM_MODEL_NAME=Qwen2.5-7B-Instruct
```

Если vLLM работает в той же Docker network, лучше обращаться по имени сервиса:

```env
LLM_PROVIDER_URL=http://vllm:8001
```

## Что важно сказать ChatGPT при помощи с переносом

Можно дать ему этот документ и попросить:

1. подготовить production-ready `docker-compose.gpu.yml`;
2. добавить vLLM service с NVIDIA GPU;
3. настроить API container на GPU embeddings, если нужно;
4. подсказать backup/restore для PostgreSQL и Qdrant;
5. добавить Alembic migrations;
6. добавить persistent model cache volume для Hugging Face models.

## Риски и TODO

Текущие ограничения:

- нет Alembic migrations;
- ingestion worker пока placeholder;
- embeddings model скачивается при первом запуске;
- Qdrant collection dimension должна совпадать с `EMBEDDING_DIMENSIONS`;
- большие datasets в `data/` могут не быть частью git;
- production auth/security пока отсутствуют;
- debug endpoints открыты без авторизации.

Рекомендуемые улучшения перед production:

- добавить Alembic;
- вынести ingestion в worker queue;
- добавить auth;
- добавить backup/restore scripts;
- добавить healthchecks для API/Qdrant/PostgreSQL;
- добавить persistent Hugging Face cache;
- добавить GPU-aware compose override;
- добавить monitoring/logging.

