# Learning Progress

## Keyword filtering

- Demonstrated understanding: Learner connected full-text matching to greater coverage, accepted extra candidates, and assigned final relevance assessment to the model. Identified a need for equivalent-term groups.
- Demonstrated understanding: Learner chose a dictionary to associate each configured topic with equivalent keyword terms.
- Needs reinforcement: Full-text availability, alias representation, and word-boundary matching remain unresolved.

## Persistent article records

- Demonstrated understanding: Learner identified metadata for articles and processing status to distinguish cooldown from waiting-for-delivery after restart.
- Introduced: A cooldown requires a stored expiration time; pending delivery requires preserving the generated summary across restarts.
- Needs reinforcement: Complete stored fields, timestamp meaning, and database choice remain unresolved.

## News processing layers

- Demonstrated understanding: Learner separated source collection and keyword filtering, model evaluation, logging, and Discord delivery into distinct responsibilities with a directional flow.
- Demonstrated understanding: Learner connected API delivery results to logging updates and proposed bounded retries followed by requeueing.
- Demonstrated understanding: Learner separated delivery availability from collection availability and used stored results to preserve work during delivery outages.
- Demonstrated understanding: Learner weighed automatic recovery's reduced manual effort against extra requests and selected periodic checks.
- Needs reinforcement: Transient versus persistent delivery failures and retry delays remain unresolved. Keyword filtering may exclude relevant articles before model evaluation.

## Duplicate prevention and delivery

- Demonstrated understanding: Learner connected the timing of recording a posted link to the risk of losing a notification when delivery fails.
- Needs reinforcement: Recording after delivery does not guarantee exactly-once delivery if the bot stops between sending and saving.

## Relevance reevaluation

- Demonstrated understanding: Learner connected temporary rejection memory to avoiding immediate repeated work while allowing changing preferences; chose priority reevaluation after preference changes.
- Needs reinforcement: Cooldown duration and handling failed reevaluations remain unresolved.

## News storage implementation

- Confirmed: Learner authorized the proposed first storage step, including separate publication/delivery times, saved content and summaries, cooldown/retry times, and pending/cooldown/ready/posted states.
- Implemented: Python Article and ArticleStatus, SQLite ArticleStore, exact-URL deduplication, guarded state transitions, rejection expiry/reset, persisted reevaluation priority, retry scheduling, and posted timestamps.
- AI implementation detail: Explicit timezone-aware UTC timestamps, database constraints, and one connection per thread.
- Verification: All seven unittest tests passed on Python 3.14.7, covering file-backed reopen behavior, duplicate prevention, cooldowns and reset priority, queued retries, invalid transitions, and timezone handling.
- Limits: Collection, evaluation, external messaging, bounded retry orchestration, and recovery probes remain unimplemented. Sending and saving are not atomic. No pending approval checkpoint.

## Content collection implementation

- Confirmed: Learner authorized feed collection, configurable alias groups, full-text filtering with partial-content fallback, abstract-based paper previews, content-aware storage migration, and focused tests. Initial enabled publications are Yle, Ars Technica, The Guardian, and arXiv; HS/io-tech/Quanta still need integration verification.
- Implemented: JSON configuration, keyword matching, RSS/Atom parsing with feedparser, HTML main-body extraction with Trafilatura, pending-candidate storage, and one-pass CLI. Full-paper downloading and model/external-message integration remain outside this step.
- Implemented: Full article / partial article / research abstract labels and delivery notices. Partial news cannot have a generated summary; abstract previews and full articles require summaries before delivery readiness.
- Implemented: Transactional legacy SQLite migration, preserving metadata, status, summaries, cooldowns, retry timestamps, and reevaluation priority.
- Implemented: Priority reevaluations defer new collection; per-source errors are logged; existing URLs skip article retrieval; basic tracking URL cleanup precedes deduplication.
- Verification: All 22 tests passed, including original storage coverage, file-backed migration, extraction/fallback, alias boundaries, RSS/Atom handling, abstract notices, and source failure isolation. pip check found no broken requirements; CLI help worked.
- Live verification limitation: Sandboxed DNS was unavailable. An approved network trial parsed the Yle feed but stalled retrieving its first article and was interrupted. The full configured source set and end-to-end live collection are not verified.
- No pending approval checkpoint. Next development area is local model relevance evaluation and summarization.

## Model relevance evaluation

- Learner design: Evaluate substantive coverage of selected keyword topics; use structured JSON for rejection/posting content; use a more lenient relevance threshold for partial news. Partial news remains summary-free; research abstracts permit labeled previews.
- Learner requirements: Consider publication recency and prior posting; exclude unreliable or missing publication dates to reduce unwanted volume. Semantic novelty comparison needs further design and is deferred.
- Approved AI additions: 3-day news age, 14-day research age, 24-hour rejection cooldown, configurable. JSON has rejected/reason/summary. Invalid/failed model responses remain pending. Python supplies original title, source, URL, and notices.
- Implemented: Shared deterministic date policy; collector excludes undated, future, and outdated items before body retrieval, using publication dates rather than update-only timestamps. Existing pending exclusions return to cooldown without calling the model.
- Implemented: Local Ollama HTTP client using a JSON schema and strict response validation, source data separated from instructions, complete-response checks, and conservative input/context budget. Prompts distinguish full articles, partial news, and abstracts.
- Implemented: Evaluator processes pending items in stored priority order, queues accepted items, cools down rejected ones, preserves pending state on model failure, and stops on server outage or failed priority evaluation.
- Implemented: --evaluate, --model, and --limit-items command options; JSON configuration and README instructions. No additional dependencies needed.
- Verification: All 39 unittest tests passed on Python 3.14.7. Tests cover request protocol, strict JSON, date gates/boundaries, state persistence, failures, and priority, alongside prior collection/storage/migration tests. CLI help succeeded; missing-model invocation returned the expected clear configuration error (exit 2).
- Limits: Ollama executable and working NVIDIA driver are unavailable here; live model quality and GPU suitability are unverified. Long inputs remain pending rather than silently shortened. Information novelty comparison, Discord delivery, Telegram alerts, scheduled orchestration, and jobs remain unimplemented.
- No pending approval checkpoint. Next work can address local model setup/testing or Discord delivery design.

## Collection fetch deadline correction

- Diagnostic evidence: Learner's Ctrl+C traceback showed article retrieval blocked in socket.connect. Previously sequential collection could remain silent for minutes, and timeout=15 applied to blocking operations rather than a whole fetch.
- Confirmed: Learner authorized a 15-second whole-fetch deadline, per-URL and elapsed-time logging, existing excerpt fallback on article timeout, and cancellation/persistence tests.
- Implemented: HTTP retrieval runs in a standard-library Python worker, bounded by parent subprocess communication deadline. Expired or interrupted workers are killed and reaped. Parent performs per-host spacing and keeps database access separate from the worker.
- Implemented: Feed-start URLs, request-start URLs/deadlines, elapsed outcomes, and per-feed stored counts. CLI Ctrl+C exits with code 130 and reports saved committed articles.
- Verification: All 44 tests passed, including real child-process cancellation of simulated 30-second connect/read stalls at a 0.5-second test deadline, successful response transfer, error/size handling, excerpt fallback, and persisted records after interruption. Existing collection/evaluation/storage/migration tests still pass.
- Live verification complete: One item per feed against a temporary database finished in about 80 seconds. Five Yle/Guardian feed requests stopped at 15.0 seconds each; Ars RSS and article retrieval succeeded, and both arXiv feeds loaded. Three inspected items did not match the configured keywords, so no candidates were stored. Exit 1 correctly reported the five source failures. The learner's default database was not used.
- Limits: The 15-second deadline is per fetch, not per collection pass or extraction. Many slow URLs can still make the total run last minutes. Whole source-set accessibility and live model quality remain separate concerns.
- No pending approval checkpoint.

## IPv4 collection correction

- Diagnostic evidence: Yle and Guardian HTTP requests succeeded over IPv4 in approximately 0.05 seconds but timed out over IPv6. The first arXiv entry had no configured keyword match, whereas two of the first ten did.
- Confirmed: Learner authorized configurable collection address-family selection, defaulting to IPv4 for this setup, preserving whole-fetch deadlines and keyword rules.
- Implemented: HttpConfig and JSON http.address_family with ipv4/auto choices. Collector passes this to HttpFetcher and the isolated worker. The worker scopes IPv4 address resolution to its own request, including redirects; auto retains the normal resolver. Parent/Ollama connections and system networking remain unchanged.
- Verification: All 48 tests passed; four address-family tests also passed after assertion formatting cleanup. Tests cover selected resolver family, socket options, restoration after failure, worker request scope, config defaults/overrides, and forwarding. Existing deadline/cancellation tests remain passing.
- Live verification: --limit-per-source 10 against a temporary database completed in about 39 seconds with discovered=80, stored=17, duplicates=1, unmatched=57, abstracts=3, source_errors=0, date_excluded=5. All eight configured feeds loaded. The database contains 14 full news articles and 3 research abstracts, all pending evaluation.
- Learner's default database was not used for live verification. To test their model, collect into the default database and then evaluate with --model qwen3-local.
- No pending approval checkpoint. Live model quality, Discord/Telegram integration, and scheduled orchestration remain unfinished.
