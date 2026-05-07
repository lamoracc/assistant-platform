# Generic Platform Audit

This document records the current source-specific hardcoding review for
`assistant-platform`. The goal is to keep the platform generic while allowing
source-specific parser support through explicit, isolated adapters or future
profiles.

## Current Conclusion

The core retrieval path is now mostly generic:

- prompts are generic and configurable;
- exact duplicate and near-duplicate dedupe use content, not filenames;
- ranking boosts use title, heading, breadcrumbs, body text, and chunk type;
- context preferences are derived from generic query/metadata signals, such as
  acronyms or identifiers, not from OPERA/PMS-specific dictionaries;
- topic-focus and unrequested-context ranking signals are generic: concise
  topic metadata is preferred for broad setup/configuration queries, while
  unrelated integration/interface/import/export or vendor-style context is
  lightly penalized only when the user did not ask for that context;
- retrieval-only answer formatting is generic and focuses extractive answers on
  the top-ranked source;
- golden retrieval cases are regression fixtures, not platform logic;
- optional reranker is disabled unless configured;
- OPERA is described as the current sample corpus, not as platform behavior.

Remaining source-specific areas are mostly documentation examples, fixtures, and
HTML extraction heuristics for legacy help exports.

## Background Ingestion Jobs Check

The first background ingestion implementation is generic:

- job records store `source_path`, optional `source_name`, and free-form
  metadata;
- statuses and progress counters are domain-neutral;
- the worker calls existing generic folder ingestion code;
- no OPERA/PMS terms are used in job model, API, worker, or service logic;
- OPERA remains only the current sample corpus documented elsewhere.

No new source-specific hardcoding was introduced in the job API or worker.

## Retrieval-Only Answer Formatting Check

The compact retrieval-only answer formatter is generic:

- it is used only when no LLM provider is configured;
- it does not contain OPERA/PMS-specific terms or branching;
- `Short answer` and `Relevant facts` are extracted from the primary top-ranked
  source, or chunks from the same `source_file`/document;
- `Top sources` may list additional unique sources, but lower-ranked secondary
  sources do not contribute facts to the human-readable answer;
- duplicate and near-duplicate content is suppressed in the answer text;
- diagnostics behavior remains controlled by the existing `debug` flag.

## Reviewed Areas

Reviewed from the current local working copy:

- ingestion services;
- extractors;
- sanitizers/normalization;
- chunking;
- retrieval/ranking/deduplication;
- prompts;
- API endpoints;
- config/env;
- tests;
- README and overview docs;
- metadata handling.

This review is kept in sync with the current repository state and should be
updated whenever generic retrieval, ingestion, prompt, or source-profile logic
changes.

## A. Acceptable Source-Specific Code

These are acceptable for the current corpus as long as they stay isolated and do
not affect generic ranking/prompting logic.

- `apps/api/app/services/document_extractors.py`
  - HTML extraction prefers `div#Layer1` when present, with fallback to the
    document body. This is a legacy exported-help heuristic, not a domain rule.
  - HTML chrome cleanup removes generic navigation/footer/print/email controls.
  - Breadcrumb extraction reads `div.breadcrumbs`, a common docs pattern.
- README examples that mention `/data/opera_full`
  - Acceptable as sample-corpus operational notes when labeled as the current
    sample corpus.
- Tests that use OPERA-like sample text
  - Acceptable as regression fixtures, but should be balanced with neutral
    fixtures over time.

## B. Undesirable Hardcode Found And Addressed

- `apps/api/app/services/prompt_builder.py`
  - Previous OPERA/PMS-specific system prompt and retrieval-only text were
    removed.
  - Prompt behavior now uses generic settings:
    `ASSISTANT_SYSTEM_PROMPT` and `PRESERVE_SOURCE_TERMS_INSTRUCTION`.
- Retrieval ranking
  - Filename is not used for metadata boost.
  - Content fingerprint dedupe does not use filename.
- README and platform overview
  - Updated to describe OPERA as the current sample corpus, not as generic
    platform logic.

## C. Should Move To Settings Or Profiles

These are not urgent bugs, but they should become explicit profiles before the
platform hosts many corpora.

- Parser/source profile:
  - HTML main-content selectors such as `div#Layer1`.
  - Source-specific chrome cleanup.
  - Source-specific image filtering rules.
- Prompt profile:
  - Assistant role.
  - Citation style.
  - Source-term preservation rules.
  - Language policy.
- Ranking profile:
  - Dataset-specific boosts, if ever needed.
  - Source trust/priority rules.
- Metadata profile:
  - Product, team, owner, visibility, source type, refresh policy.
  - Breadcrumb/section normalization.

## Current Architecture Summary

- `apps/api/app/api`: HTTP routers for upload, import, chat, and diagnostics.
- `apps/api/app/services/file_router.py`: file routing by extension/content type.
- `apps/api/app/services/document_extractors.py`: extraction and metadata.
- `apps/api/app/services/chunking.py`: heading/paragraph chunking.
- `apps/api/app/services/normalization.py`: text/chunk-type normalization.
- `apps/api/app/services/ingestion.py`: PostgreSQL + Qdrant ingestion.
- `apps/api/app/services/retrieval.py`: vector/keyword retrieval, exact dedupe,
  filters, ranking, reranking, near-dedupe, diagnostics.
- `apps/api/app/services/retrieval_ranking.py`: generic ranking boosts.
- `apps/api/app/services/reranker.py`: optional CrossEncoder reranker.
- `apps/api/app/services/prompt_builder.py`: generic prompt construction and
  retrieval-only answer formatting.

## Documentation Consistency Notes

Updated docs should state:

- `.env` is the operational Docker Compose env file.
- `.env.example` is a tracked template/reference.
- `RETRIEVAL_CANDIDATE_K`, `RETRIEVAL_KEYWORD_CANDIDATE_K`, and
  `RETRIEVAL_FINAL_K` are separate.
- `RETRIEVAL_TOP_K` is legacy/fallback.
- Deduplication is retrieval-only.
- OPERA is the current sample corpus.
- Reranker is optional and falls back safely.
- Debug diagnostics include retrieval/ranking/dedupe fields.
- Retrieval-only fallback answers are compact and based on the primary source,
  while `sources` still exposes final retrieval results.
- Golden retrieval cases exist as unit-level regression fixtures and should be
  expanded without turning sample-corpus examples into generic platform rules.

## Recommended Next Changes

1. Add source model tables before importing unrelated corpora.
2. Add source-level authorization before company-wide usage.
3. Add a `SOURCE_PROFILE` / `PARSER_PROFILE` config and move legacy HTML
   selectors there.
4. Expand golden retrieval coverage with neutral fixtures and future corpora.
5. Split sample-corpus runbooks from the generic README if corpus-specific
   instructions grow.
6. Run retrieval evaluation in CI and track trend metrics.
