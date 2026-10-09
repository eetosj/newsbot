# Project Map

## Purpose
Personal Discord bot that collects relevant news and finds new matching job openings.

## Requirements
- Consider publication recency and whether an item has already been posted. Learner also wants information novelty considered, but comparison with earlier coverage needs further design and remains open.
- News should come from the last few days; research papers may be one to two weeks old. Implemented configurable limits default to 3 days for news and 14 days for research, approved by the learner.
- Exclude items without a reliable publication date to avoid an influx of undated content.
- Include Finnish and English content. Learner explicitly requests Yle and Helsingin Sanomat; prefer publications without paywalls and cover technology, general news, and science.
- Expand collection to new research papers, with relevance assessment and summaries. The initial paper source is arXiv; abstract previews are supported by collection, while full-paper retrieval remains a later step.
- Learner explicitly permits abstract-based research-paper previews, labeled separately from full-paper summaries. This exception does not permit summarizing partial news articles.
- If full article text cannot be retrieved, keyword-match the available parts. The model still evaluates relevance using the title and available excerpt, but does not summarize. Relevant items are posted with a clear indication that full text was not retrieved and access may be restricted.
- News in selected fields, including computer science, AI, and mathematics.
- Check job postings at configurable intervals and return new openings matching user parameters.
- Personal use through Discord.
- Post relevant news to one Discord channel and matching job openings to another.
- Model assesses relevance and summarizes both articles and job postings for faster reading.
- News comes from a curated list of local and global publications; specific sources remain undecided.
- Job searches initially focus on Finland, with geography configurable for global opportunities.

## Proposed Choices
- Learner proposed model evaluation of substantial coverage of configured keywords, returning JSON with rejection information and proposed posting content. Partial news should have a more lenient relevance threshold. Implemented JSON schema has rejected (boolean), reason (string), and summary (string or null), with content-specific validation. Publication recency and prior posting can be checked directly in Python; semantic novelty comparison remains an open design question.
- Researched candidates: Yle (official current feed directory), HS (requested but current feed/access unverified), io-tech (Finnish technology; feed linked on its forum), Ars Technica (documented technology/science feeds; full-text RSS is subscriber-only), The Guardian (general/technology/science RSS), Quanta (science/mathematics/computer-science coverage; feed endpoint could not be validated), and arXiv (open research papers with category RSS/Atom). Yle, Ars Technica, The Guardian, and arXiv are now configured following implementation approval. HS, io-tech, and Quanta remain candidates requiring verification.
- Learner proposed a collection layer that checks selected websites and filters articles by relevant keywords, then sends candidates to the model. The model evaluates relevance and categorizes candidates as post-ready or discarded. A logging layer records results and passes post-ready items to the Discord bot for delivery. Collection and storage interfaces are implemented; model client and evaluation interfaces are implemented; delivery integration remains unresolved. Discord API delivery results will determine posted-state updates.
- Learner suggested using Ollama with a local model running on their NVIDIA GPU. Intended model responsibilities are relevance assessment and summarization; HTTP integration is implemented; installed model choice, live quality, and GPU suitability remain unresolved.

## Chosen Approach
- Inspect the full article for keyword matches, allowing incidental mentions through for model evaluation. Configure equivalent terms as a dictionary mapping a topic key to its synonyms, such as AI and artificial intelligence. Implemented with Unicode normalization, case-insensitive word/phrase boundaries, and an editable JSON dictionary; Finnish inflections need explicit aliases.
- Use SQLite for persistent storage.
- Use Python; implement the news feature first.
- Implemented feed-based collection with the approved initial Yle, Ars Technica, Guardian, and arXiv configuration. The eight configured feeds and a ten-item sample per feed are now verified through collection over IPv4.
- Implemented exact-URL storage deduplication and guarded posted-state transition. Future Discord integration must call it only after confirmed delivery.
- Rejected items have a temporary cooldown rather than a permanent rejection. Implemented configurable default: 24 hours, approved by the learner.
- Implemented cooldown reset and persisted priority, with new collection deferred until priority reevaluation completes. Model reevaluation is implemented; automatic preference-change detection remains unimplemented.
- Discord delivery result determines logging updates. Retry failed sends a few times, then put persistently failing items back in the queue. Retry count, delay, and failure classification remain unresolved; not implemented.
- On delivery failures requiring intervention, pause Discord delivery and report a clear error. Healthy collection and evaluation continue, storing results for later delivery. Periodically check whether posting is available and resume automatically when it is. Check method and interval remain unresolved; not implemented.
- Report errors in the terminal and send outage alerts through Telegram, which the learner already uses. Not implemented; Telegram credentials and recipient configuration remain unset.

## Components
- Implemented: `newsbot/articles.py` defines article records and processing statuses.
- Implemented: `newsbot/storage.py` persists records and guards transitions using SQLite.
- `tests/test_storage.py` verifies storage behavior; `README.md` explains the lifecycle and test command.
- Implemented: `newsbot/config.py`, `newsbot/keywords.py`, and `newsbot/collection.py` load source/keyword configuration, retrieve RSS/Atom and article text, and store matching candidates.
- Implemented: `newsbot/__main__.py` runs a single collection pass; `config.example.json` enables the approved initial source set.
- Implemented: `newsbot/recency.py` provides shared publication age checks.
- Implemented: `newsbot/ollama.py` validates structured local-model responses; `newsbot/evaluation.py` applies decisions to article storage.
- CLI evaluation options process stored pending articles with a configured model.
- Discord delivery, Telegram alerts, scheduling, and jobs remain unimplemented.

## Main Flow
Proposed by learner: configured feed/API sources → collection and keyword filtering → model relevance evaluation and summarization → post-ready / discarded results → logging layer → Discord bot → news channel.
Chosen delivery constraint: Discord delivery result → logging update; save posted status only after successful delivery. Failed sends receive bounded retries, then requeue if still unsuccessful.

## Data and Trust Boundaries
Persistent article records will use SQLite to retain metadata and processing status across restarts. Content-aware schema and its legacy migration are implemented; credentials handling remains unresolved.

## Build and Deployment
Python 3.11+; standard-library-only storage. Collection dependencies are in `requirements.txt` and installed in `.venv`. Verified command: `.venv/bin/python -m unittest discover -s tests -v` (48 passing tests on Python 3.14.7). CLI: `.venv/bin/python -m newsbot --config config.example.json --database newsbot.sqlite3`. Deployment remains undecided.

## Unknowns
Local model selection and GPU suitability; live feed/article access; semantic novelty comparison; summary quality; Discord/Telegram setup; scheduling and automatic preference-change detection; additional news sources; job sources and matching parameters; deployment.

## Implemented Storage
- Learner approved the first storage step. Article records contain exact URL, title, source, retrieved content, publication time, generated summary, status, Discord posting time, cooldown expiry, and next delivery-attempt time.
- AI-added implementation detail: A persisted priority flag orders reevaluation ahead of new pending items; the future orchestration layer must drain those items before collection.
- Timestamps are timezone-aware UTC. Failed delivery retains ready status and summary. Successful delivery must be confirmed by the caller before marking posted.
- Cooldown resets change only rejected items; queued and posted records remain intact.

## Implemented Collection
- Full article extraction → keyword candidates, or title/excerpt fallback → keyword candidates. Research feeds provide labeled abstracts without full-paper downloads. Stored candidates enter pending state; the implemented evaluator can reject them or queue them for delivery.
- Stored content kind controls summary eligibility and delivery notices. Existing SQLite records migrate without losing their processing state.
- Configuration changes are editable JSON; keyword/criteria change detection is not automated yet. The future orchestration layer must call cooldown reset when preferences change.
- Extraction is heuristic and cannot prove that every article body is complete. Initial live retrieval stalled on IPv6; the IPv4 correction subsequently loaded all configured feeds and saved 17 matching candidates from an 80-item sample.

## Implemented Evaluation
- Shared publication-date filtering with 3-day news and 14-day paper defaults; unknown/future dates are excluded. Evaluation rechecks dates for previously stored candidates. Already queued or posted articles are outside pending evaluation.
- Ollama uses local /api/chat, a JSON schema, and strict validation. Accepted full news and abstracts require a summary; partial news and rejected items require null. Python retains trusted original posting metadata and derives content notices.
- Accepted candidates move to ready; rejected/date-excluded pending candidates enter the configurable 24-hour cooldown. API/validation failures leave pending records unchanged; server outages and priority failures stop the batch.
- Model name is intentionally unset until chosen locally. The example context request is 16384 tokens; conservative prompt budgeting leaves oversized inputs pending. Actual tokenizer behavior, model context support, quality, and GPU memory fit still need live verification.
- Semantic novelty comparison remains a separate open design topic. Rejection reasons are logged to terminal rather than a new database field.

## Implemented Fetch Deadlines
- `newsbot/_fetch_worker.py` isolates standard-library HTTP I/O. The parent in `collection.py` enforces a 15-second complete-fetch deadline and kills/reaps expired or interrupted workers.
- Progress logs identify URLs, elapsed times, feed progress, and candidates stored. Failed article fetches retain the excerpt fallback; failed feeds do not stop other sources.
- Ctrl+C exits cleanly with code 130; committed records remain saved. Host spacing and extraction are outside the per-fetch deadline. Collection still processes sources/items sequentially.
- Controlled cancellation and persistence tests pass. Live deadline check completed: five Yle/Guardian feed requests timed out at 15.0 seconds; Ars RSS/article and both arXiv feeds loaded. One item per successful feed yielded no keyword matches. Full source accessibility remains unresolved, separately from cancellation behavior.

## Implemented IPv4 Selection
- `http.address_family` defaults to `ipv4`; `auto` retains system dual-stack selection. The selected family is logged and applied only in the isolated HTTP worker, including redirects.
- The full collection sample passed over IPv4: 80 items inspected, 17 candidates stored (14 news articles, 3 abstracts), zero source errors, about 39 seconds. Verification used a temporary database.
- Four family-selection tests plus existing tests pass (48 total). Default source configuration is verified for feed retrieval and sample article extraction; HS/io-tech/Quanta still require their own integrations.
