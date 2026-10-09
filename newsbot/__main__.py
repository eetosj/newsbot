"""Collect candidates or evaluate stored candidates with local Ollama."""

import argparse
import logging
from dataclasses import asdict, replace

from .collection import Collector
from .config import load_config
from .evaluation import Evaluator
from .storage import ArticleStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect keyword-matching news and paper abstracts")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--database", default="newsbot.sqlite3")
    parser.add_argument("--limit-per-source", type=int, help="Optional limit for a short trial run")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate stored pending items instead of collecting")
    parser.add_argument("--model", help="Installed Ollama model to use for evaluation")
    parser.add_argument("--limit-items", type=int, help="Maximum pending items to evaluate")
    args = parser.parse_args()
    if args.limit_per_source is not None and args.limit_per_source < 1:
        parser.error("--limit-per-source must be positive")
    if args.limit_items is not None and args.limit_items < 1:
        parser.error("--limit-items must be positive")
    if not args.evaluate and (args.model is not None or args.limit_items is not None):
        parser.error("--model and --limit-items require --evaluate")
    if args.evaluate and args.limit_per_source is not None:
        parser.error("--limit-per-source applies only to collection")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        config = load_config(args.config)
        if args.model is not None:
            config = replace(config, ollama=replace(config.ollama, model=args.model))
    except (OSError, ValueError, TypeError) as error:
        parser.error(f"Invalid configuration: {error}")
    if args.evaluate and config.ollama.model is None:
        parser.error("Set ollama.model in configuration or pass --model with an installed model name")
    with ArticleStore(args.database) as store:
        if args.evaluate:
            result = Evaluator(config, store).evaluate_pending(limit=args.limit_items)
            failed = result.errors > 0
        else:
            result = Collector(config, store).collect(limit_per_source=args.limit_per_source)
            failed = result.source_errors > 0
    logging.info("%s result: %s", "Evaluation" if args.evaluate else "Collection", asdict(result))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Interrupted; committed articles remain saved")
        raise SystemExit(130)
