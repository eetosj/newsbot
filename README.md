# Personal Discord news bot

Implemented: configurable RSS/Atom collection, article-text extraction, keyword
filtering, research abstract collection, and persistent SQLite article state.
Local Ollama relevance evaluation, source verification, and bounded summary revisions
are implemented and tested with live samples. Model judgments can still be wrong.
Discord delivery, Telegram
alerts, and job searches come later.

Requires Python 3.11 or newer. Set up a project-local environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Collect content

Edit `config.example.json`, or save your own JSON configuration and pass its path:

```sh
.venv/bin/python -m newsbot --config config.example.json --database newsbot.sqlite3
```

For a short trial, add `--limit-per-source 1`. This is a one-pass collector, not a
scheduled Discord bot yet. It stores matching candidates pending model evaluation,
and does not send external messages. Source failures are logged independently;
the command exits with code 1 if any feed failed, even if other feeds succeeded.

The `keywords` dictionary maps each topic to a list of equivalent search terms.
Both keys and terms match case-insensitively at word/phrase boundaries. Finnish
inflections need explicit terms (for example `tekoäly`, `tekoälyn`, `tekoälyä`);
matching does not stem words automatically.

Sources have `name`, `url`, optional `kind` (`news` or `research`), and optional
`enabled` (default true). The example enables Yle, Ars Technica, Guardian, and
arXiv. HS, io-tech, and Quanta remain candidates in [SOURCES.md](SOURCES.md).

- News: attempt HTML main-body extraction, then filter the title and body. If
  extraction fails, filter the title and feed excerpt instead.
- Research: filter the title and feed abstract, without downloading the full paper.
- Before downloading articles, reject feeds' undated, future-dated, or outdated
  items. A modification date alone does not establish publication recency.
- The default age limits are 3 days for news and 14 days for papers, inclusive.
- Common tracking parameters and fragments are removed before duplicate checks;
  semantic query parameters are preserved. Existing URLs are not fetched again.
- Priority reevaluations block new collection until the evaluation layer handles
  them. Run evaluation first after resetting cooldowns, honoring the preference-change
  ordering requirement.

Requests have timeouts, size limits, and per-host spacing. Full-article extraction
is best effort: it rejects declared paywalls and very short extracted teasers, but
cannot guarantee every webpage's article is complete. There is no login or browser
automation fallback. Full-paper HTML/PDF retrieval is outside this step.

Each HTTP fetch has a **15-second whole-fetch deadline**, enforced by a separate
Python worker process. Expired or interrupted workers are killed and reaped, so
DNS, repeated connection attempts, redirects, and body reads cannot keep that
fetch running indefinitely. Article failures use the existing title/excerpt
fallback; feed failures are logged and the collector continues to other sources.
The worker uses the same Python interpreter and adds no dependencies.

Collection defaults to IPv4 because this setup's IPv6 connections to Yle and
Guardian stall while IPv4 succeeds. Set `"http": {"address_family": "auto"}`
in your JSON configuration to use normal system IPv4/IPv6 selection elsewhere.
The setting applies inside the isolated collection worker, including redirected
requests. It does not change system networking or the Ollama client's connections.
The selected family appears in each fetch-start log.

Terminal logs show each feed, each fetched URL, elapsed fetch times, and per-feed
stored counts. Ctrl+C exits cleanly with code 130, retaining already committed
articles. The deadline covers fetching, not extraction or the complete collection
pass: `--limit-per-source 10` applies to each feed and can still take several
minutes across many unavailable articles. Use `--limit-per-source 1` for a short
diagnostic run. Host spacing is separate from the HTTP deadline.

## Evaluate pending content

Run Ollama on your machine and select an installed model using `ollama.model` in
your configuration or the `--model` option. Replace `YOUR_MODEL` with that name:

```sh
.venv/bin/python -m newsbot --evaluate --model YOUR_MODEL --limit-items 10
```

This project's configuration now selects the installed non-thinking model
`qwen3-instruct-local`. To evaluate a small batch with that default:

```sh
.venv/bin/python -m newsbot --evaluate --limit-items 3
```

The local validation returned a six-token JSON probe with no thinking trace.
It then evaluated a Finnish article and an English research abstract successfully
in a temporary database, with both responses ending normally. See
[MODEL_SETUP.md](MODEL_SETUP.md) for the selected model and verification details.

Evaluation operates on the existing database; it does not collect new feeds or
send Discord/Telegram messages. It checks publication age again so candidates
that waited too long cannot enter the delivery queue. Already queued and posted
items are not reevaluated. Date-excluded existing pending items return to cooldown
without a model call, so they do not permanently block priority reevaluation.

The `evaluation` section configures `news_max_age_days`, `research_max_age_days`,
and `rejection_cooldown_hours` (default 24). The `ollama` section configures `model`,
`base_url` (default `http://localhost:11434`), `timeout_seconds`, and `context_tokens`.
It also includes `think`, a boolean defaulting to `false`: supported models are
explicitly asked not to generate a thinking trace for these evaluations. Set it
to `true` to compare reasoning-enabled behavior later. Request logs show the
thinking setting; response logs show elapsed time, finish reason, and generated
token count. Incomplete responses still remain pending.
The selected model has been exercised locally; GPU suitability remains unverified.

The local client uses Ollama's [structured-output API](https://docs.ollama.com/capabilities/structured-outputs).
The required decision is a JSON object with exactly these fields:

```json
{"rejected": false, "reason": "Substantial discussion of AI", "summary": "A concise summary."}
```

- The model accepts central or substantial shared topic coverage, including
  technology legislation and social effects, and rejects incidental mentions.
  Partial news uses a more lenient
  threshold based on a concrete title/excerpt connection.
- Rejected items require `summary: null` and enter the configured cooldown.
- Accepted partial news requires `summary: null` and keeps its access notice.
- Accepted full news and paper abstracts receive a separate source-verification
  pass before their summary or preview can enter the delivery queue. Summaries stay
  within 1500 characters and use the source language.
- Source text is placed in a separate data message. Prompts instruct the model to
  ignore embedded instructions and avoid inventing information. Python supplies
  original titles, links, sources, and content notices; the model cannot override
  them through extra JSON fields.
- Reasons and summaries must use only supplied source information, without internet
  retrieval or facts from the model's background knowledge. Prompt rules preserve
  actors, attribution, meaning, uncertainty, and limitations, keep allegations as
  allegations, and prohibit unsupported causal or topic connections. These are
  model instructions; JSON validation does not verify factual accuracy.
- Malformed JSON, inconsistent decisions, unfinished responses, or request failures
  leave items pending and produce an error log. Server failures stop the batch;
  failed priority reevaluations also stop it so lower-priority items cannot bypass them.
- The same local model checks each draft in a fresh request containing the source
  and draft. Failed checks return specific issues to a separate revision request.
  Up to three revisions are allowed after the initial draft: at most four drafts
  are checked. Passing summaries are queued; four failed checks queue the relevant
  item's original link without a summary, with a saved omission notice. Source
  content kinds remain intact. API errors or invalid responses leave the article
  pending instead of triggering this fallback. Logs and result counters report
  verification checks, revisions, and omitted summaries.
- Verification reduces errors but the same model can repeat or overlook them.
  Existing ready items are not automatically rechecked. Discord delivery remains
  unimplemented; this step prepares the delivery queue.
- Full inputs are never silently shortened by this client. A conservative UTF-8
  byte budget reserves 2048 context tokens for output and formatting. Items exceeding
  it remain pending; increase `context_tokens` only to a value supported by your
  chosen model and hardware. This budget is not a model-specific tokenizer check.

The command exits with code 1 when an evaluation fails. Rejection reasons appear
in terminal logs; they are not stored as a separate database field. Comparing
information novelty with previously posted coverage remains unimplemented.

Run the tests from the repository root:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

## Article lifecycle

`pending → cooldown → pending` for irrelevant articles, or
`pending → ready → posted` for relevant articles.

- `ArticleStore.add()` inserts metadata and retrieved content with an exact, unique
  URL. Duplicate collection preserves existing state.
- `reject()` sets a cooldown expiration. `pending()` releases expired cooldowns.
- `reset_cooldowns()` returns rejected articles to pending when preferences change.
  They appear first in `pending()`, even after a restart. The future orchestration
  layer must process them before collecting new content.
- `mark_ready()` persists the generated summary for delivery. Partial news must
  queue without a summary; full articles and research abstracts require one unless
  explicitly queued with `summary_omitted=True` after verification exhaustion.
- `defer_delivery()` retains a queued summary and schedules its next attempt.
- `mark_posted()` must be called only after a successful Discord response.
- All timestamps must include a timezone and are stored in UTC. Publication and
  Discord delivery times are separate.

Use `ArticleStore` as a context manager to close the connection. The database's
parent directory must exist. A store connection is for one thread at a time, and
database transactions should never wrap external HTTP or model calls.

Records distinguish `full_article`, `partial_article`, and `research_abstract`.
`delivery_notice` provides an access warning for partial news and an abstract-only
label for paper previews. The future delivery layer must include these notices.
Existing first-version databases migrate transactionally on opening, preserving
metadata, cooldowns, summary queues, retry times, priority, and posted state.

Storage does not verify Discord delivery itself. Sending and saving are separate
operations: a crash after sending but before saving can still cause a duplicate.
URL tracking variants are distinct unless the future collector normalizes them.
