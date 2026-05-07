# Generic Platform Audit

This audit records the current generic architecture review and the first safe
retrieval-quality changes. The project is a company assistant platform for
routine operational work. The current first dataset is OPERA documentation, but
the core ingestion, retrieval, prompting, and API behavior should remain
domain-agnostic.

## Current Architecture

- `apps/api/app/api` exposes upload, folder import, chat, and debug endpoints.
- `apps/api/app/services/file_router.py` routes files by extension and content
  type, not by business domain.
- `apps/api/app/services/document_extractors.py` extracts PDF, DOC/DOCX, HTML,
  Markdown, and text documents into normalized text blocks and metadata.
- `apps/api/app/services/chunking.py` chunks extracted blocks by headings and
  paragraphs.
- `apps/api/app/services/normalization.py` classifies chunks as content,
  procedure, table, reference, navigation, or empty.
- `apps/api/app/services/ingestion.py` stores all indexed documents in
  PostgreSQL and all vectors in Qdrant. Deduplication is retrieval-only.
- `apps/api/app/services/retrieval.py` retrieves vector and keyword candidates,
  merges exact content duplicates, ranks candidates, optionally reranks, removes
  near-duplicates, and returns final sources.
- `apps/api/app/services/prompt_builder.py` builds prompts or retrieval-only
  output when no LLM provider is configured.

## Hardcoding Review

### A. Acceptable Source-Specific Code

These are acceptable as first-dataset support, but should remain isolated from
generic ranking and prompting.

- `apps/api/app/services/document_extractors.py`
  - HTML extraction prefers `div#Layer1` when present. This is a legacy help
    export pattern and is safe as an HTML extractor heuristic because it falls
    back to the body when absent.
  - HTML cleanup removes page chrome such as navigation, footer, print, and
    email controls. The rules are generic enough for exported documentation.
  - Breadcrumb extraction reads `div.breadcrumbs`. This is a common docs
    pattern and feeds metadata, not domain-specific behavior.
- `tests/test_markdown_extractor.py`
  - Uses OPERA-like fixture metadata and text. This is acceptable as a fixture,
    but future tests should add neutral examples too.
- README examples that mention `/data/opera_full` are acceptable as dataset
  runbooks if clearly framed as the first imported dataset, not platform logic.

### B. Undesirable Hardcode Found

- `apps/api/app/services/prompt_builder.py`
  - The system prompt and retrieval-only fallback were OPERA/PMS-specific.
  - This has been changed to generic configurable prompts.
- `README.md`
  - Several examples still describe OPERA-specific imports and questions.
  - This is acceptable for current operations, but should be moved into a
    source-specific runbook section as the platform grows.
- `PLATFORM_OVERVIEW.md`
  - Mentions OPERA as the first dataset. This is okay, but it should not imply
    the platform itself is OPERA-only.
- `tests/test_generic_reranking.py`
  - One fixture body says "OPERA PMS packages". The assertion tests generic
    ranking behavior, but the fixture should eventually be neutralized.

### C. Should Move To Profiles Or Settings

- Parser/source profile:
  - HTML selector preferences such as `div#Layer1`.
  - Source-specific page chrome cleanup if future sources need different
    rules.
- Prompt profile:
  - Assistant role, source terminology preservation, citation style, and any
    company/domain language.
- Ranking profile:
  - Generic defaults now apply globally. Future source-specific boosts should be
    explicit profile settings, never hardcoded in retrieval logic.
- Metadata profile:
  - Source-specific fields such as product path, section group, visibility, and
    connector ownership should be normalized through a profile contract.

## Retrieval Patch Summary

Implemented safe generic changes:

- Split candidate retrieval count from final source count.
  - `RETRIEVAL_CANDIDATE_K` controls vector candidates before ranking.
  - `RETRIEVAL_KEYWORD_CANDIDATE_K` controls keyword candidates before ranking.
  - `RETRIEVAL_FINAL_K` controls final sources returned to the user.
  - `RETRIEVAL_TOP_K` remains as a backward-compatible fallback.
- Added a reranker layer.
  - If `RERANKER_MODEL_NAME` is unset, retrieval behaves like before.
  - If set, a `sentence_transformers.CrossEncoder` reranker is loaded lazily.
  - Load failure falls back to generic ranking and logs the exception.
- Added metadata-aware ranking.
  - Exact query phrase matches in title, heading, or breadcrumbs get the
    strongest boost.
  - Exact phrase matches in body get a smaller boost.
  - Multi-term query phrases are kept important for technical expressions such
    as "package codes", "rate code", or "reservation reinstate".
  - Filename is not used as the primary duplicate key and is not used for
    metadata boosts.
- Expanded diagnostics.
  - Debug diagnostics now expose retrieval-stage score, keyword score, metadata
    boosts, reranker score when present, final score, and filtered reason.

## Backward Compatibility

- Existing documents and vectors remain in PostgreSQL and Qdrant.
- No ingestion-time document deletion or storage deduplication was added.
- The current `/data/opera_full` import remains valid.
- If `LLM_PROVIDER_URL` is missing, retrieval-only mode still works.
- If no reranker model is configured, the platform uses generic ranking only.

## Follow-Up Refactor Plan

1. Add explicit `SOURCE_PROFILE` or `PARSER_PROFILE` configuration.
2. Move legacy HTML selector preferences into profile data.
3. Split README into platform guide and dataset-specific runbooks.
4. Add neutral fixtures alongside first-dataset fixtures.
5. Add an optional prompt profile file for company-specific assistant behavior.
6. Add a ranking-profile config if future datasets need source-specific boosts.

## Verification Commands

Run from `/home/lamorac/assistant-platform`:

```bash
python3 -m unittest discover -s tests -v
docker compose exec -T api python -m py_compile \
  app/core/config.py \
  app/services/retrieval.py \
  app/services/retrieval_ranking.py \
  app/services/reranker.py \
  app/services/prompt_builder.py
docker compose exec -T api sh -c 'PYTHONPATH=/app python /tmp/test_metadata_aware_ranking.py -v'
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/debug/qdrant
```
