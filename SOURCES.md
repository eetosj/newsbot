# News and research source candidates

Researched 2026-10-09. Initial configuration now enables Yle, Ars Technica, The Guardian, and arXiv. HS, io-tech, and Quanta remain candidates.
The learner requested Yle and HS; the other publications are suggestions.
Documented feeds still need actual fetch/parser checks before integration.

| Source | Role | Evidence and limitations |
| --- | --- | --- |
| Yle | Finnish general and science news | [Official RSS directory](https://yle.fi/rss) lists current general, science, and English feeds. |
| Helsingin Sanomat | Requested Finnish general/science source | Browsing could not retrieve its homepage or proposed RSS endpoint. Current feed support and article access remain unverified. |
| io-tech | Finnish technology, especially hardware | [Site](https://www.io-tech.fi/) exposes article text. Its [forum](https://bbs.io-tech.fi/threads/palautetta-io-tech-fi-sivustosta-uutiset-ja-artikkelit-v0-1.7548/page-2) links `https://www.io-tech.fi/feed/`; actual feed fetch remains untested. |
| Ars Technica | English technology and science | [Official feed directory](https://arstechnica.com/rss-feeds/). [Subscription features](https://arstechnica.com/subscribe/) include full-text RSS; do not assume public feeds contain full articles. |
| The Guardian | English general, technology, and science | [Official RSS guide](https://www.theguardian.com/help/feeds). Website and app access differ; [publisher explanation](https://www.theguardian.com/help/insideguardian/2023/mar/22/an-update-on-reader-support-via-the-app) describes open website journalism. Actual extraction still needs checking. |
| Quanta Magazine | Mathematics, computer science, and science journalism | [About](https://www.quantamagazine.org/about/). Candidate worth investigating; attempted feed endpoint could not be validated by browsing. |
| arXiv | New computer science, AI, mathematics, and statistics papers | [RSS/Atom documentation](https://info.arxiv.org/help/rss.html) supports subject-specific feeds. [About](https://info.arxiv.org/about/index.html) documents open access and clarifies that arXiv itself does not peer-review submissions. |

## Research-paper decision still open

The learner wants research papers processed too and explicitly permits labeled
abstract-based previews. A feed abstract is not the full paper. Full-paper HTML/PDF
retrieval remains outside the proposed initial collection step.

## Other source researched

[ScienceDaily](https://www.sciencedaily.com/newsfeeds.htm) documents science feeds
with headlines, summaries, and links. It is an additional candidate if broader
science coverage is wanted; not selected.

## Integration verification

The initial collector configuration is in `config.example.json`. During the live
trial, Yle's feed parsed, but article retrieval stalled and the trial was
interrupted. The complete source set is not yet verified end to end. Local fixture
and migration tests pass. Publication access can differ from feed availability.

## IPv4 validation

After diagnosing unreachable IPv6 connections, the collector now defaults to
IPv4 (`http.address_family` can be set to `auto`). A live ten-item-per-feed check
loaded all eight configured feeds and inspected 80 items in about 39 seconds,
saving 14 news articles and 3 abstracts with zero source errors. Verification
used a temporary database. HS, io-tech, and Quanta remain separate candidates.
