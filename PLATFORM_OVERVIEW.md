# Platform Overview

## Purpose

`assistant-platform` is an internal assistant platform for company routine work.
The current implementation focuses on document ingestion, retrieval, and
retrieval-grounded answers. This is the foundation for a broader assistant that
can later connect to internal systems, summarize work sources, and execute
approved repetitive tasks.

The current first corpus is OPERA PMS documentation. It is a useful large
dataset for validating ingestion and retrieval quality, but it is not the
platform domain. Generic platform behavior must continue to work with many
future corpora: product docs, SOPs, support playbooks, internal policies,
meeting notes, task systems, email summaries, and other company knowledge.

## What Exists Today

The project currently provides:

- FastAPI API service.
- Docker Compose environment with API, PostgreSQL, Qdrant, Redis, and a simple
  ingestion worker.
- Multi-format document extraction for HTML/HTM, PDF, DOC/DOCX, Markdown, and
  TXT.
- Folder import for large documentation trees.
- Background ingestion jobs with persistent job status and cooperative
  cancellation.
- PostgreSQL storage for documents, chunks, metadata, links, and image assets.
- Qdrant vector storage for chunk retrieval.
- Multilingual embeddings, default `BAAI/bge-m3`.
- Compact retrieval-only answer mode when no LLM provider is configured.
- Optional OpenAI-compatible LLM provider.
- Generic retrieval ranking with metadata-aware boosts.
- Retrieval-stage exact duplicate and near-duplicate filtering.
- Optional CrossEncoder reranker layer.
- Debug diagnostics explaining retrieval, ranking, dedupe, and final results.
- Focused unit tests for ingestion batching, ranking, exact dedupe, and
  near-duplicate dedupe.
- A small unit-level golden retrieval regression suite for known high-value
  questions.

## Architecture

### API Layer

`apps/api/app/api` contains HTTP routers:

- `GET /health`
- `POST /documents/upload`
- `POST /documents/import-folder`
- `POST /chat/query`
- `GET /debug/qdrant`
- `POST /debug/qdrant/smoke-test`
- `POST /debug/import-folder`
- `POST /debug/extract-file`
- `POST /debug/chunks`
- `POST /debug/search`

### Service Layer

Important services:

- `file_router.py`: routes files by extension/content type.
- `document_extractors.py`: extracts text, metadata, links, and image refs.
- `normalization.py`: sanitizes text and classifies chunk type.
- `chunking.py`: chunks extracted blocks.
- `ingestion.py`: stores documents/chunks and upserts vectors.
- `embeddings.py`: loads and runs the embedding model.
- `qdrant_store.py`: manages Qdrant collection and upserts.
- `retrieval.py`: vector search, keyword fallback, dedupe, ranking, reranking,
  diagnostics, and final source assembly.
- `retrieval_ranking.py`: generic score boosts and ranking details.
- `reranker.py`: optional CrossEncoder reranker with no-op fallback.
- `prompt_builder.py`: generic prompt construction and compact retrieval-only
  answer formatting.
- `llm_provider.py`: optional OpenAI-compatible chat completion provider.

### Storage Layer

- PostgreSQL stores source documents, chunks, metadata, links, and image assets.
- Qdrant stores vectors and payloads for chunk search.
- Redis is present as queue/cache infrastructure. The first background
  ingestion worker uses PostgreSQL polling instead of Celery/RQ.

## Ingestion Flow

Single-document ingestion:

1. Receive file upload.
2. Route by file type.
3. Extract text blocks and document metadata.
4. Chunk text by headings and paragraphs.
5. Drop `navigation` and `empty` chunks.
6. Embed all remaining chunks.
7. Store document and chunks in PostgreSQL.
8. Upsert vectors into Qdrant.
9. Store links and image asset metadata where available.

Folder ingestion:

1. Walk files recursively.
2. Route each file as text document, image asset, ignored support asset, or
   unsupported.
3. For supported text documents, extract and chunk content.
4. Accumulate chunks across multiple documents.
5. Flush embeddings in batches controlled by `INGESTION_EMBED_BATCH_CHUNKS`.
6. Commit documents/chunks and Qdrant vectors by batch.
7. Resolve document links and referenced images after documents exist.
8. Log progress and final stats.

Duplicate file hashes are skipped at ingestion time, but existing chunks can be
reindexed into Qdrant. Retrieval dedupe does not remove anything from storage.

Background ingestion jobs:

1. `POST /ingestion-jobs` creates a row in `ingestion_jobs`.
2. The worker polls PostgreSQL for `pending` jobs.
3. The worker claims a job, marks it `running`, and calls the same
   `ingest_help_folder` service used by the sync endpoint.
4. Progress fields are updated between files: total, processed, failed, and
   current file.
5. Cancellation is cooperative through `cancel_requested` and is checked between
   files.
6. Failed/canceled jobs can be retried from the beginning; existing file hashes
   keep retries from duplicating already indexed documents.

## Retrieval Flow

The retrieval pipeline is content-first and generic:

1. Sanitize the question.
2. Embed the question.
3. Retrieve vector candidates from Qdrant with `RETRIEVAL_CANDIDATE_K`.
4. Retrieve keyword candidates from PostgreSQL with
   `RETRIEVAL_KEYWORD_CANDIDATE_K`.
5. Filter low vector scores using `RETRIEVAL_MIN_SCORE`.
6. Merge candidates and exact-dedupe by normalized content fingerprint.
7. Apply request filters.
8. Rank candidates with generic metadata/body/chunk-type signals.
9. Optionally rerank using CrossEncoder.
10. Group near-duplicate body content.
11. Return `RETRIEVAL_FINAL_K` final sources.
12. Build context for LLM or compact retrieval-only answer.

This split lets the platform retrieve a wider candidate set while keeping final
answers concise.

## Ranking Behavior

Ranking is generic and domain-agnostic. It considers:

- original vector/keyword score;
- exact full-query phrase in title, heading, or breadcrumbs;
- exact full-query phrase in body;
- multi-term query phrases;
- query-term coverage in metadata and body;
- generic query context terms such as acronyms/identifiers as metadata
  preferences, not domain-specific body boosts;
- focused topic metadata, so concise titles/headings that closely match a
  generic setup/configuration query can outrank narrower context pages;
- a small generic penalty for unrequested integration/interface/import/export,
  external-system, or vendor-style context when the user did not ask for that
  context;
- chunk type preference for `procedure`, `content`, and `table`;
- penalties for navigation/reference/empty chunks;
- penalties for very short or very long chunks.

Filename is not used as the primary duplicate key and is not used for metadata
boosts. It is still kept for diagnostics and source display.

This ranking layer is deliberately heuristic and domain-agnostic. For example,
a generic "configure X" query should prefer general "X setup" pages, while a
query that explicitly asks for an interface, export, import, or external system
workflow can still surface those more specific pages.

## Reranking Behavior

`RERANKER_MODEL_NAME` is optional.

- Empty value: no-op reranker, generic ranking remains final.
- Configured value: `sentence_transformers.CrossEncoder` scores
  `(question, chunk text)` pairs for the top `RERANKER_TOP_N` candidates after
  generic ranking. The reranker score is used as a weighted boost, so generic
  metadata/ranking signals remain the main relevance guardrail.
- Load failure: exception is logged and retrieval falls back to generic ranking.

This means the platform can run without a reranker on CPU-only or minimal
deployments. On CPU-only deployments, keep `RERANKER_TOP_N` bounded to avoid
large latency spikes.

## Deduplication Behavior

Exact duplicate dedupe:

- retrieval-only;
- uses normalized heading plus normalized content;
- keeps the highest-scoring candidate;
- preserves chunks with same heading but different content;
- preserves different chunks from the same document;
- reports `duplicate_content` diagnostics.

Near-duplicate dedupe:

- retrieval-only;
- runs after ranking and optional reranking;
- compares normalized body content using token shingles;
- ignores front matter, breadcrumbs, image-only lines, navigation, markdown
  URLs, and punctuation noise;
- default threshold is `0.92`;
- keeps one candidate per group by default;
- can prefer the candidate whose metadata, breadcrumbs, section/title context,
  or breadcrumb-like body context matches query context terms;
- reports `near_duplicate_content`, `duplicate_of`, and `similarity`.

## Answer Formatting

When `LLM_PROVIDER_URL` is configured, the API builds a bounded context from
retrieved chunks and sends it to the provider.

When no LLM provider is configured, `/chat/query` stays useful in
retrieval-only mode. Instead of returning a long concatenation of raw chunks,
the `answer` field is formatted as:

- `Short answer`
- `Relevant facts`
- `Top sources`

The short answer and facts are extractive and intentionally conservative. They
are built only from the primary top-ranked source, or chunks from the same
`source_file`/document as that primary result. This keeps fallback answers
focused and avoids mixing unrelated facts from lower-ranked sources. The
separate `sources` JSON array still returns the final retrieved sources for
inspection or UI rendering.

## Debug Diagnostics

Debug mode shows:

- candidate limits: `retrieval_k`, `keyword_retrieval_k`, `final_k`;
- collection and threshold settings;
- raw vector scores;
- retrieval-stage score;
- keyword score;
- metadata boosts;
- reranker score when used;
- final score;
- ranking reasons;
- filtered reasons;
- duplicate target and similarity where relevant.

These fields are intended to explain why a chunk was retrieved, boosted,
removed, or returned.

## Current Sample Corpus Status

Last verified corpus:

- path: `/data/opera_full`
- processed files: `10768`
- indexed documents: `9346`
- skipped empty/noisy files: `1422`
- failed files: `0`
- chunks/vectors: `34511`
- Qdrant vector size: `1024`

Treat this as the first loaded knowledge source, not as a product boundary.

## Strengths

- Clear service separation around extraction, chunking, embeddings, storage,
  retrieval, ranking, and prompting.
- Real end-to-end RAG flow already works.
- Handles large folder imports better after batch embedding changes.
- Keeps dedupe retrieval-only, preserving source documents and vectors.
- Supports multilingual embeddings and retrieval-only fallback.
- Provides useful debug diagnostics for retrieval quality work.
- Keeps optional reranker and LLM provider backward-compatible.

## Limitations

- Synchronous folder import still exists for compatibility.
- Background ingestion uses simple DB polling, not a robust distributed queue.
- Cancellation is cooperative and is checked between files, not in the middle of
  long embedding batches.
- Retry restarts from the source path and relies on duplicate file hashes; it is
  not true checkpoint resume yet.
- No Alembic migrations.
- No users, roles, source permissions, or audit trail.
- No formal source model for multiple corpora and visibility rules.
- Keyword fallback uses broad SQL `ILIKE`.
- Golden retrieval coverage exists, but it is still small and unit-level rather
  than a production corpus evaluation with recall/source-accuracy metrics.
- Reranker is not enabled by default and needs latency/quality testing.
- Retrieval-only answers are compact extractive fallbacks and do not perform
  LLM-style multi-source synthesis.
- No production observability for latency, throughput, and model behavior.
- Parser/source/prompt/ranking profiles are still planned, not implemented.

## Prioritized Roadmap

1. Alembic migrations. This should come before more schema growth because the
   platform now has persistent document, chunk, asset, and ingestion-job state.
2. Knowledge-source model with owner, source type, collection, visibility, and
   refresh policy.
3. Authentication, authorization, and source-level access control.
4. Harden ingestion jobs with safer multi-worker claiming, heartbeat/stale-job
   recovery, per-file error records, and true checkpoint resume.
5. Better lexical search, starting with PostgreSQL full-text search.
6. Expand retrieval evaluation from the current small golden suite into a
   broader CI-tracked evaluation set with source accuracy and recall metrics.
7. Reranker evaluation profile for quality and latency.
8. Parser/source profiles to isolate legacy HTML and corpus-specific rules.
9. Observability: structured logs, metrics, tracing, and latency breakdowns.
10. Assistant profiles, conversations, and safe tool/action execution for
    routine company workflows.
