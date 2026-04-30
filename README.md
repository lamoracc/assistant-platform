# Assistant Platform

A scalable project skeleton for a RAG-based work assistant.

## Stack

- FastAPI backend
- Qdrant vector database
- PostgreSQL relational database
- Redis cache and queue foundation
- `BAAI/bge-m3` multilingual embeddings
- Docker Compose for local development

## Project Layout

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
├── .env.example
├── docker-compose.yml
└── README.md
```

## Environment

Copy the sample environment file when you want a local baseline:

```bash
cp .env.example .env
```

Docker Compose already provides the same defaults to the services:

- `DATABASE_URL`: SQLAlchemy PostgreSQL connection string
- `QDRANT_URL`: Qdrant API URL
- `QDRANT_COLLECTION_NAME`: vector collection for document chunks
- `EMBEDDING_MODEL_NAME`: sentence-transformers model name
- `EMBEDDING_DIMENSIONS`: vector size for the Qdrant collection
- `HF_HOME`: Hugging Face cache root inside containers
- `TRANSFORMERS_CACHE`: Transformers model cache path
- `SENTENCE_TRANSFORMERS_HOME`: sentence-transformers cache path
- `UPLOAD_MAX_BYTES`: maximum accepted upload size
- `RETRIEVAL_TOP_K`: number of chunks to retrieve for chat queries
- `RETRIEVAL_MIN_SCORE`: minimum Qdrant similarity score for vector hits
- `MAX_CONTEXT_CHARS`: maximum retrieved context passed to answer generation
- `LLM_PROVIDER_URL`: optional OpenAI-compatible local LLM base URL
- `LLM_MODEL_NAME`: optional model name for the configured LLM provider

## Setup

Build and start the platform:

```bash
docker compose up --build
```

This skeleton currently uses SQLAlchemy `create_all` at startup. If you already
have an older local Postgres volume from a previous scaffold, reset volumes
before testing schema changes:

```bash
docker compose down -v
docker compose up --build
```

The default embedding model is now `BAAI/bge-m3` with 1024-dimensional vectors.
If your local Qdrant collection was created with the previous 384-dimensional
model, recreate the Qdrant volume before reindexing:

```bash
docker compose down -v
docker compose up --build
```

The first `BAAI/bge-m3` load may take several minutes because the model must be
downloaded from Hugging Face. Docker Compose mounts a named `hf_cache` volume at
`/root/.cache/huggingface` in the API and worker containers, so rebuilds and
restarts should reuse the downloaded model instead of fetching it again.

Inspect the model cache size:

```bash
docker compose exec api du -sh /root/.cache/huggingface
```

## Embedding Profiles

The embedding model is controlled by environment variables, so you can switch
profiles without changing application code. Docker Compose keeps the persistent
`hf_cache` volume for both profiles.

### Dev CPU Profile

Use this on a laptop or slow CPU-only VM. It is lighter and faster to download
and run, but produces 384-dimensional vectors.

```env
EMBEDDING_MODEL_NAME=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_DIMENSIONS=384
```

### Production GPU Profile

Use this on a stronger VM, ideally with GPU acceleration. It is the default
profile and produces 1024-dimensional multilingual vectors.

```env
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024
```

`BAAI/bge-m3` is too heavy for many slow CPU-only VMs: first load can take
several minutes, indexing can feel stalled, and CPU/RAM pressure may be high.
For local development, prefer the dev CPU profile.

Important: Qdrant collection vector size must match `EMBEDDING_DIMENSIONS`.
If you switch between 384 and 1024 dimensions, recreate/reindex the Qdrant
collection or reset local volumes:

```bash
docker compose down -v
docker compose up --build
```

Run in the background:

```bash
docker compose up --build -d
```

Check the API health endpoint:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

Open service UIs and endpoints:

- API docs: http://localhost:8000/docs
- Qdrant REST API: http://localhost:6333
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

## Document Ingestion

The API accepts PDF, DOC, DOCX, HTML, HTM, MD, and TXT files at
`POST /documents/upload`.

```bash
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@/path/to/document.pdf"
```

Upload an OPERA PMS HTML documentation page:

```bash
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@/path/to/opera-page.html"
```

The ingestion flow:

1. Route files by extension and MIME type.
2. Extract text and metadata from HTML, HTM, PDF, DOC, DOCX, MD, or TXT files.
3. Normalize and sanitize text.
4. Detect language per document and chunk.
5. Chunk extracted content by headings and paragraphs.
6. Classify chunks as `content`, `procedure`, `table`, `reference`,
   `navigation`, or `empty`.
7. Skip `navigation` and `empty` chunks.
8. Generate normalized multilingual embeddings with `BAAI/bge-m3`.
9. Store document and chunk metadata in PostgreSQL.
10. Initialize and upsert vectors into the configured Qdrant collection.

For OPERA PMS HTML documentation, ingestion uses BeautifulSoup to parse
`div#Layer1`, removes navigation and page chrome, preserves list/link text, and
stores HTML metadata:

- `source_filename`
- `title`
- `breadcrumbs`
- `links`
- `image_refs`
- `support_asset_refs`
- `main_heading`
- `document_type = html`

## OPERA Help Folder Import

Import a full OPERA PMS help folder from a path available inside the API
container:

```bash
curl -X POST http://localhost:8000/documents/import-folder \
  -H "Content-Type: application/json" \
  -d '{"path":"/path/to/opera-help"}'
```

The folder importer walks recursively and routes files by extension:

- `.htm` and `.html`: parsed with BeautifulSoup using `div#Layer1` when present.
- `.pdf`: parsed with the PDF extractor, preserving page numbers and page image hints when available.
- `.doc` and `.docx`: parsed through the Word extractor. Legacy `.doc` uses a best-effort printable-text fallback.
- `.md` and `.txt`: parsed as Markdown/plain text.
- `.jpg`, `.jpeg`, `.png`, and `.gif`: stored as image assets, not text chunks.
- `.css` and `.js`: ignored as standalone files, but preserved as HTML support references when linked.

For HTML pages, the importer stores page links in `document_links` and resolves
parent-child relationships to target document IDs when linked pages are part of
the same import. Referenced images are stored in `image_assets` with path,
filename, dimensions when readable, hash, and `referenced_by_document_id`.

Tiny UI images and likely icons/logos are skipped by size or filename. Documents
and image assets are hashed with SHA-256 so repeated imports skip duplicates.
Progress is logged by the API process during the import.

## RAG Chat Querying

Ask questions against the ingested OPERA PMS knowledge base:

```bash
curl -X POST http://localhost:8000/chat/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I configure packages in OPERA PMS?"}'
```

Enable retrieval diagnostics:

```bash
curl -X POST http://localhost:8000/chat/query \
  -H "Content-Type: application/json" \
  -d '{"question":"package configuration","debug":true}'
```

The query pipeline:

1. Embed the question with the configured `sentence-transformers` model.
2. Search Qdrant collection `document_chunks` with `top_k=8` by default.
3. Filter vector results below `RETRIEVAL_MIN_SCORE`, default `0.55`.
4. Run a PostgreSQL keyword fallback over chunk content, headings, filenames,
   source paths, and HTML title metadata.
5. Apply optional metadata filters.
6. Retrieve up to 40 vector candidates and 40 keyword candidates.
7. Merge and deduplicate candidates by `document_id + chunk_index`.
8. Apply generic ranking signals:
   exact phrase matches, strong query-term coverage, title/heading matches over
   body-only matches, useful chunk types, and focused chunk length.
9. Pass final candidates through a reranker-ready interface.
10. Build a bounded context from retrieved chunks.
11. Return an LLM answer when `LLM_PROVIDER_URL` is configured.
12. Return a retrieval-only answer with sources when no LLM endpoint is configured.

Russian questions use the multilingual embedding model to retrieve English
documentation. When an LLM provider is configured, Russian questions instruct
the assistant to answer in Russian while preserving OPERA UI terms, menu names,
field labels, and button names in English.

When `debug=true`, the response includes retrieval diagnostics with raw vector
scores, filtered reasons, collection name, `top_k`, and `min_score`. If all
vector scores are below threshold and no keyword fallback matches, `sources`
will be empty.

Example response:

```json
{
  "answer": "No LLM provider is configured, so this is a retrieval-only response...",
  "sources": [
    {
      "document": "packages/package_configuration.htm",
      "source_file": "packages/package_configuration.htm",
      "heading": "Package Configuration",
      "chunk_index": 0,
      "score": 0.82
    }
  ]
}
```

To use a local OpenAI-compatible Qwen/vLLM server:

```bash
LLM_PROVIDER_URL=http://host.docker.internal:8001 \
LLM_MODEL_NAME=Qwen2.5-7B-Instruct \
docker compose up --build
```

The provider abstraction currently targets `/v1/chat/completions`, which works
with vLLM's OpenAI-compatible API.

## Qdrant Debugging

Check the configured Qdrant collection:

```bash
curl http://localhost:8000/debug/qdrant
```

Insert one known-good test vector:

```bash
curl -X POST http://localhost:8000/debug/qdrant/smoke-test
```

Ingestion logs each Qdrant upsert with collection name, point count, first point
ID, vector dimension, and the upsert result. If a document already exists in
PostgreSQL by file hash, re-running folder import reindexes its existing chunks
back into Qdrant instead of silently skipping vector storage.

Additional diagnostics:

```bash
curl -X POST http://localhost:8000/debug/extract-file \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/opera_docs/OperaHelp/example.htm"}'

curl -X POST http://localhost:8000/debug/chunks \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/opera_docs/OperaHelp/example.htm"}'

curl -X POST http://localhost:8000/debug/search \
  -H "Content-Type: application/json" \
  -d '{"question":"Как настроить packages в OPERA PMS?","filters":{"chunk_type":"procedure"}}'
```

Example response:

```json
{
  "document_id": "4d0a8f03-6861-4636-8fb1-a68d5bff5e6d",
  "filename": "document.pdf",
  "content_type": "application/pdf",
  "chunk_count": 12,
  "status": "ingested"
}
```

Stop services:

```bash
docker compose down
```

Remove local volumes:

```bash
docker compose down -v
```

## Next Steps

- Add API routers for assistants, conversations, and retrieval.
- Add database migrations with Alembic.
- Move ingestion into the worker using Redis, Celery, RQ, or a lightweight async queue.
- Add shared configuration and observability across apps.
