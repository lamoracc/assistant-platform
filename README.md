# Assistant Platform

`assistant-platform` is a generic document ingestion and retrieval API for an
internal company assistant. It currently runs a RAG flow over one loaded corpus
of OPERA PMS documentation, but OPERA is a sample/current dataset, not platform
logic. The same ingestion, retrieval, ranking, and answer pipeline is intended
to work with other documentation and operational knowledge sources.

For product context and roadmap, see [PLATFORM_OVERVIEW.md](PLATFORM_OVERVIEW.md).
For the latest generic-platform hardcoding audit, see
[GENERIC_PLATFORM_AUDIT.md](GENERIC_PLATFORM_AUDIT.md).

## Stack

- FastAPI backend
- PostgreSQL for documents, chunks, metadata, links, and image assets
- Qdrant for vector search over document chunks
- Redis as the queue/cache foundation
- `sentence-transformers` embeddings, default `BAAI/bge-m3`
- Optional OpenAI-compatible LLM provider
- Optional `sentence-transformers` CrossEncoder reranker
- Docker Compose for local/VM deployment

## Project Layout

```text
.
|-- apps
|   |-- api
|   |   |-- app
|   |   |   |-- api
|   |   |   |-- core
|   |   |   |-- models
|   |   |   |-- services
|   |   |   `-- main.py
|   |   |-- Dockerfile
|   |   `-- requirements.txt
|   `-- worker-ingest
|       |-- Dockerfile
|       |-- main.py
|       `-- requirements.txt
|-- data
|-- tests
|-- .env.example
|-- docker-compose.yml
|-- README.md
|-- PLATFORM_OVERVIEW.md
`-- GENERIC_PLATFORM_AUDIT.md
```

## Environment Files

Docker Compose automatically reads `.env` from the project root for variable
substitution in `docker-compose.yml`. `.env.example` is only a tracked template
and reference.

Use this to create a local working env file:

```bash
cp .env.example .env
```

If a variable is written in Compose as `${NAME:-default}`, the value comes from
`.env` when present and falls back to `default` otherwise. Changing `.env`
requires recreating the relevant service:

```bash
docker compose up -d --force-recreate api
```

Python code changes mounted under `apps/api:/app` are picked up by Uvicorn
reload, but already-running processes do not reload environment variables.

## Key Environment Variables

Core services:

- `DATABASE_URL`: SQLAlchemy PostgreSQL URL.
- `REDIS_URL`: Redis URL for future queue/cache work.
- `QDRANT_URL`: Qdrant API URL.
- `QDRANT_COLLECTION_NAME`: vector collection name, default `document_chunks`.
- `UPLOAD_MAX_BYTES`: max upload size for single-file upload.

Embeddings and ingestion:

- `EMBEDDING_MODEL_NAME`: `sentence-transformers` embedding model.
- `EMBEDDING_DIMENSIONS`: Qdrant vector size. Must match the embedding model.
- `EMBEDDING_BATCH_SIZE`: embedding encode batch size.
- `INGESTION_EMBED_BATCH_CHUNKS`: folder-import embedding batch size across
  documents.
- `INGESTION_WORKER_POLL_SECONDS`: DB polling interval for the ingestion worker.
- `HF_HOME`, `TRANSFORMERS_CACHE`, `SENTENCE_TRANSFORMERS_HOME`: model cache
  locations inside containers.

Retrieval and ranking:

- `RETRIEVAL_TOP_K`: legacy final result fallback.
- `RETRIEVAL_CANDIDATE_K`: vector candidates fetched before ranking/reranking.
- `RETRIEVAL_KEYWORD_CANDIDATE_K`: keyword candidates fetched before
  ranking/reranking.
- `RETRIEVAL_FINAL_K`: final sources returned to the user.
- `RETRIEVAL_MIN_SCORE`: minimum vector similarity for vector hits.
- `RETRIEVAL_NEAR_DUPLICATE_DEDUPE`: enable retrieval-stage near-duplicate
  grouping.
- `RETRIEVAL_NEAR_DUPLICATE_THRESHOLD`: similarity threshold, default `0.92`.
- `RETRIEVAL_MAX_NEAR_DUPLICATES_PER_GROUP`: kept candidates per near-duplicate
  group, default `1`.

LLM and prompt behavior:

- `LLM_PROVIDER_URL`: optional OpenAI-compatible `/v1/chat/completions` base URL.
- `LLM_MODEL_NAME`: model name sent to the provider.
- `ASSISTANT_SYSTEM_PROMPT`: optional generic system prompt override.
- `PRESERVE_SOURCE_TERMS_INSTRUCTION`: optional instruction for preserving
  product names, UI labels, and source terms.

Reranker:

- `RERANKER_MODEL_NAME`: optional CrossEncoder model, for example
  `BAAI/bge-reranker-v2-m3`.
- `RERANKER_BATCH_SIZE`: reranker batch size.

If `LLM_PROVIDER_URL` is empty, `/chat/query` still works in retrieval-only
mode and returns a compact structured answer built from the top retrieved
source. If `RERANKER_MODEL_NAME` is empty or fails to load, retrieval falls
back to generic ranking.

## Setup

Build and start:

```bash
docker compose up --build -d
```

Check health:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/debug/qdrant
```

Useful URLs:

- API docs: http://localhost:8000/docs
- Qdrant REST API: http://localhost:6333
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

Important: Qdrant collection vector size must match `EMBEDDING_DIMENSIONS`. If
you switch embedding models with a different dimension, create a new collection
or intentionally reset/reindex data. Do not reset volumes on a populated system
unless you mean to delete local indexed data.

## Ingestion Flow

Single document upload:

```bash
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@/path/to/document.pdf"
```

Folder import from a path visible inside the API container:

```bash
curl -X POST http://localhost:8000/documents/import-folder \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/my_documents"}'
```

The ingestion pipeline:

1. Route files by extension and MIME/content type.
2. Extract text blocks and metadata from supported document formats.
3. Normalize and sanitize text.
4. Remove or classify navigation, empty, and low-value chunks.
5. Chunk content by headings and paragraphs.
6. Enrich chunk metadata, including language and chunk type.
7. Skip `navigation` and `empty` chunks.
8. Batch embeddings across documents for folder imports.
9. Store documents and chunks in PostgreSQL.
10. Upsert chunk vectors and payloads into Qdrant.
11. Store document links and referenced image assets when available.

Supported text formats include HTML/HTM, PDF, DOC/DOCX, Markdown, and TXT.
Images referenced by documents can be stored as assets, but image files are not
currently OCR-indexed as text chunks.

The folder importer hashes files. If a document with the same hash already
exists, it skips duplicate ingestion and reindexes existing chunks into Qdrant
instead of silently losing vector storage.

## Background Ingestion Jobs

The synchronous `POST /documents/import-folder` endpoint remains available for
compatibility. New large imports should use background ingestion jobs so the API
request can return immediately and progress can be inspected.

Create a job:

```bash
curl -X POST http://localhost:8000/ingestion-jobs \
  -H "Content-Type: application/json" \
  -d '{
    "source_path": "/data/my_documents",
    "source_name": "my-docs",
    "metadata": {"source_type": "documentation"}
  }'
```

List recent jobs:

```bash
curl http://localhost:8000/ingestion-jobs
curl "http://localhost:8000/ingestion-jobs?status=running"
```

Get one job:

```bash
curl http://localhost:8000/ingestion-jobs/{job_id}
```

Cancel a running or pending job:

```bash
curl -X POST http://localhost:8000/ingestion-jobs/{job_id}/cancel
```

Retry a failed or canceled job:

```bash
curl -X POST http://localhost:8000/ingestion-jobs/{job_id}/retry
```

The `worker-ingest` service polls PostgreSQL for `pending` jobs, marks one as
`running`, and calls the same `ingest_help_folder` service used by the existing
sync endpoint. No Celery/RQ dependency is required in this first step.

Job status values:

- `pending`
- `running`
- `completed`
- `failed`
- `canceled`

Progress fields:

- `total_files`
- `processed_files`
- `failed_files`
- `current_file`
- `error_summary`
- `error_details`
- `cancel_requested`
- `created_at`, `updated_at`, `started_at`, `finished_at`

Cancellation is cooperative: the worker checks the cancel flag between files.
Already committed documents/chunks remain indexed. Per-file failures are counted
and do not necessarily fail the entire job when the existing ingestion flow can
continue safely.

## Current Sample Corpus

The current loaded corpus is OPERA PMS documentation under `/data/opera_full`.
This is the first practical knowledge source used to validate scale and
retrieval behavior. OPERA-specific examples in this repository should be read as
sample-corpus runbooks or tests, not as generic platform behavior.

Some legacy HTML pages use export-specific structures such as `div#Layer1`.
The HTML extractor treats that as a heuristic and falls back to the document
body when absent.

## Chat and Retrieval

Ask a question:

```bash
curl -X POST http://localhost:8000/chat/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I configure access permissions?","debug":true}'
```

Response behavior:

- `sources` always contains the final retrieved sources returned by retrieval.
- `debug=false` omits diagnostics and per-source `ranking_reason`.
- `debug=true` includes retrieval/ranking/dedupe diagnostics and
  `ranking_reason`.
- When an LLM provider is configured, the retrieved chunks are converted into a
  bounded prompt context for the provider.
- When no LLM provider is configured, the API returns a retrieval-only answer
  instead of dumping full chunks. The answer contains `Short answer`,
  `Relevant facts`, and `Top sources`.

Retrieval-only answer formatting:

- `Short answer` and `Relevant facts` are extracted only from the primary
  top-ranked source, or chunks from the same `source_file`/document as that
  primary result.
- `Top sources` may still list up to three unique sources for transparency.
- The `sources` JSON array is unchanged and may include up to
  `RETRIEVAL_FINAL_K` sources.
- Excerpts are sentence-based, truncated, and cleaned of breadcrumb/navigation
  noise where possible.
- Duplicate and near-duplicate source content is not repeated in the
  human-readable answer.

The retrieval pipeline:

1. Sanitize the user question.
2. Embed the question.
3. Fetch vector candidates from Qdrant using `RETRIEVAL_CANDIDATE_K`.
4. Filter vector hits below `RETRIEVAL_MIN_SCORE`.
5. Fetch keyword candidates from PostgreSQL using
   `RETRIEVAL_KEYWORD_CANDIDATE_K`.
6. Merge vector and keyword candidates.
7. Remove exact duplicate candidates by normalized chunk-content fingerprint.
8. Apply optional metadata filters.
9. Apply generic metadata-aware ranking.
10. Optionally apply a CrossEncoder reranker.
11. Group near-duplicate content after ranking/reranking.
12. Return `RETRIEVAL_FINAL_K` final sources.
13. Build bounded context for the LLM or compact retrieval-only response.

`RETRIEVAL_CANDIDATE_K` and `RETRIEVAL_FINAL_K` are intentionally separate:
retrieval can inspect a larger candidate pool while still returning a small,
readable final source list.

## Ranking and Reranking

Generic ranking uses:

- raw vector score or keyword baseline score;
- exact query phrase matches in `title`, `heading`, or `breadcrumbs`;
- exact query phrase matches in body text;
- multi-term query phrases, such as `package codes` or `reservation reinstate`;
- query-term coverage in metadata and body;
- generic context-like query terms, such as uppercase acronyms or identifiers,
  as metadata/context preferences rather than topical body boosts;
- focused topic metadata: concise titles/headings that closely match the query
  topic are preferred for generic setup/configuration questions;
- unrequested specific context: candidates that introduce extra integration,
  interface, import/export, external system, or vendor-style context can receive
  a small penalty when the user did not ask for that context;
- useful chunk types: `procedure`, `content`, `table`;
- penalties for low-value chunk types and very short/very long chunks.

Filename is not used as the primary duplicate key and is not used for metadata
boosts. Filename/path may still appear in diagnostics and keyword candidate
retrieval.

The unrequested-context penalty is generic and content-based. It is intended to
keep broad queries such as "configure transaction codes" focused on general
topic/setup pages instead of narrower integration/interface pages. If the user
explicitly asks for that context, for example an interface or export workflow,
the penalty is not applied.

Reranking is optional. With no `RERANKER_MODEL_NAME`, `get_reranker()` returns a
no-op reranker. When configured, the CrossEncoder scores `(question, chunk)`
pairs and replaces final ranking score with the reranker score. Model load
failures are logged and fall back to generic ranking.

## Deduplication

Deduplication is retrieval-only. It does not delete documents, chunks, or
vectors from PostgreSQL or Qdrant.

Exact duplicate dedupe:

- happens after vector/keyword merge;
- uses normalized heading plus normalized chunk content;
- strips markdown link syntax while preserving visible text;
- lowercases, collapses whitespace, and removes punctuation noise;
- keeps the highest-scoring candidate;
- reports `filtered_reason="duplicate_content"` in debug diagnostics.

Near-duplicate dedupe:

- happens after ranking and optional reranking;
- compares normalized body content, not filename;
- ignores YAML front matter, breadcrumbs, image-only lines, previous/next style
  navigation, markdown URLs, and punctuation noise;
- uses token-shingle similarity with default threshold `0.92`;
- keeps the best candidate in each group by score, with a generic preference
  for query context terms that match metadata, breadcrumbs, section/title
  context, or breadcrumb-like context embedded in body text;
- reports `filtered_reason="near_duplicate_content"`,
  `duplicate_of={source_file, heading, chunk_index}`, and `similarity`.

## Debug Diagnostics

Set `debug=true` on `/chat/query` to include diagnostics.

Top-level diagnostic fields include:

- `collection_name`
- `retrieval_k`
- `keyword_retrieval_k`
- `final_k`
- `top_k` as a backward-compatible alias for final count
- `vector_candidates`
- `keyword_candidates`
- `min_score`
- `filters`
- `results`
- `final_results`

Per-candidate diagnostics include:

- `document`
- `source_file`
- `heading`
- `chunk_index`
- `document_id`
- `raw_score`
- `score`
- `retrieval_type`
- `filtered_reason`
- `ranking_reason`
- `retrieval_stage_score`
- `keyword_score`
- `metadata_boosts`
- `reranker_score`
- `final_score`
- `duplicate_of` for duplicate/near-duplicate filtered candidates
- `similarity` for near-duplicate filtered candidates

## Debug Endpoints

```bash
curl http://localhost:8000/debug/qdrant
curl -X POST http://localhost:8000/debug/qdrant/smoke-test
curl -X POST http://localhost:8000/debug/extract-file \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/my_documents/example.html"}'
curl -X POST http://localhost:8000/debug/chunks \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/my_documents/example.html"}'
curl -X POST http://localhost:8000/debug/search \
  -H "Content-Type: application/json" \
  -d '{"question":"access permissions configuration"}'
```

## Current Limitations

- Folder import still runs synchronously in the API process.
- Background folder import jobs exist, but they use simple DB polling and are
  not yet a robust distributed queue.
- Job cancellation is cooperative and checked between files, not during a long
  embedding batch already in progress.
- Retry restarts a failed/canceled source path from the beginning and relies on
  file-hash duplicate skipping; it is not a true checkpoint resume.
- Database schema is still managed with SQLAlchemy `create_all`; Alembic
  migrations are not yet in place.
- There is no authentication, authorization, source-level access control, or
  audit trail.
- Keyword search is still broad `ILIKE` SQL rather than PostgreSQL full-text
  search or a dedicated lexical search engine.
- Reranker is optional and not enabled by default.
- The golden retrieval suite is still small and unit-level. It protects known
  ranking/formatting regressions, but it is not yet a full corpus evaluation
  with metrics such as recall@k, MRR, or source accuracy.
- There is no production observability layer for latency, throughput, model
  load time, and ingestion progress metrics.
- Source/parser/prompt/ranking profiles are not yet formalized.
- Retrieval-only answer generation is extractive and compact; it is useful as a
  fallback, but it is not a substitute for an LLM-generated synthesis when
  multi-source reasoning is needed.

## Prioritized Next Steps

1. Add Alembic migrations before expanding the schema further. The project now
   has persistent ingestion-job state, so schema changes should become explicit
   and repeatable.
2. Add `knowledge_sources` / source collections / visibility metadata so
   multiple corpora can coexist safely.
3. Add authentication and source-level authorization before broad company use.
4. Harden ingestion jobs with row-level claiming, heartbeat/stale-job recovery,
   richer per-file error records, and checkpoint-style resume.
5. Replace or augment `ILIKE` keyword fallback with PostgreSQL full-text search.
6. Expand the golden retrieval suite with more corpora, expected source sets,
   answer-quality checks, and CI reporting.
7. Test an optional reranker profile on the current corpus and measure latency
   versus quality.
8. Formalize parser/source profiles so legacy HTML heuristics remain isolated.
9. Add structured metrics and logs for ingestion, retrieval, reranking, and LLM
   calls.
10. Add assistant profiles, conversation history, and later a safe tool/action
    layer for routine company workflows.
