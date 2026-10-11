"""Pending articles → deterministic eligibility checks → model → stored decision."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import CollectionConfig
from .ollama import ModelError, OllamaClient, OllamaUnavailable
from .recency import exclusion_reason, utc_now
from .storage import ArticleStore


logger = logging.getLogger(__name__)
MAX_SUMMARY_REVISIONS = 3


@dataclass
class EvaluationResult:
    examined: int = 0
    accepted: int = 0
    rejected: int = 0
    date_excluded: int = 0
    errors: int = 0
    verification_checks: int = 0
    summary_revisions: int = 0
    summary_omitted: int = 0


class Evaluator:
    def __init__(self, config: CollectionConfig, store: ArticleStore, *,
                 client: OllamaClient | None = None):
        self.config = config
        self.store = store
        self.client = client if client is not None else OllamaClient(config.ollama)

    def evaluate_pending(self, *, now: datetime | None = None,
                         limit: int | None = None) -> EvaluationResult:
        if limit is not None and limit < 1:
            raise ValueError("Evaluation limit must be positive")
        now = now or utc_now()
        result = EvaluationResult()
        pending = self.store.pending(now=now)
        if limit is not None:
            pending = pending[:limit]
        for article in pending:
            result.examined += 1
            cooldown_until = now + timedelta(hours=self.config.evaluation.rejection_cooldown_hours)
            reason = exclusion_reason(article.published_at, article.content_kind,
                                      self.config.evaluation, now=now)
            if reason:
                self.store.reject(article.url, until=cooldown_until)
                result.date_excluded += 1
                logger.info("Excluded %s: %s", article.url, reason)
                continue
            try:
                decision = self.client.evaluate(article, self.config.keywords)
                summary = decision.summary
                summary_omitted = False
                if not decision.rejected and summary is not None:
                    for draft in range(MAX_SUMMARY_REVISIONS + 1):
                        check = self.client.verify(article, summary)
                        result.verification_checks += 1
                        if check.passed:
                            logger.info("Summary verified for %s (draft %s)", article.url, draft + 1)
                            break
                        logger.warning("Summary verification failed for %s (draft %s): %s",
                                       article.url, draft + 1, "; ".join(check.issues))
                        if draft == MAX_SUMMARY_REVISIONS:
                            summary = None
                            summary_omitted = True
                            break
                        summary = self.client.revise(article, summary, check.issues)
                        result.summary_revisions += 1
            except ModelError as error:
                result.errors += 1
                logger.warning("Evaluation failed for %s; remains pending: %s", article.url, error)
                # A server outage should not trigger one failed call per article.
                # Priority reevaluations must not be bypassed by lower-priority work.
                if isinstance(error, OllamaUnavailable) or article.reevaluation_priority:
                    break
                continue
            if decision.rejected:
                self.store.reject(article.url, until=cooldown_until)
                result.rejected += 1
                logger.info("Rejected %s: %s", article.url, decision.reason)
            else:
                self.store.mark_ready(article.url, summary, summary_omitted=summary_omitted)
                result.accepted += 1
                if summary_omitted:
                    result.summary_omitted += 1
                    logger.warning("Queued link only for %s after four failed summary checks", article.url)
                logger.info("Queued %s: %s", article.url, decision.reason)
        return result
