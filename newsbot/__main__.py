"""One-pass collection, evaluation, or Discord delivery."""

import argparse
import logging
from dataclasses import asdict, replace

from .collection import Collector
from .config import load_config
from .evaluation import Evaluator
from .delivery import Delivery, DeliveryResult, DeliverySetupError, DiscordSettings, format_message
from .storage import ArticleStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect keyword-matching news and paper abstracts")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--database", default="newsbot.sqlite3")
    parser.add_argument("--limit-per-source", type=int, help="Optional limit for a short trial run")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--evaluate", action="store_true", help="Evaluate stored pending items instead of collecting")
    modes.add_argument("--deliver", action="store_true", help="Send ready items to Discord in one pass")
    parser.add_argument("--preview", action="store_true", help="With --deliver, preview messages without sending")
    parser.add_argument("--model", help="Installed Ollama model to use for evaluation")
    parser.add_argument("--limit-items", type=int, help="Maximum items to evaluate or deliver")
    args = parser.parse_args()
    if args.limit_per_source is not None and args.limit_per_source < 1:
        parser.error("--limit-per-source must be positive")
    if args.limit_items is not None and args.limit_items < 1:
        parser.error("--limit-items must be positive")
    if not args.evaluate and args.model is not None:
        parser.error("--model requires --evaluate")
    if not (args.evaluate or args.deliver) and args.limit_items is not None:
        parser.error("--limit-items requires --evaluate or --deliver")
    if (args.evaluate or args.deliver) and args.limit_per_source is not None:
        parser.error("--limit-per-source applies only to collection")
    if args.preview and not args.deliver:
        parser.error("--preview requires --deliver")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        config = None if args.deliver else load_config(args.config)
        settings = DiscordSettings.from_environment() if args.deliver and not args.preview else None
        if args.model is not None:
            config = replace(config, ollama=replace(config.ollama, model=args.model))
    except (OSError, ValueError, TypeError) as error:
        parser.error(f"Invalid configuration: {error}")
    if args.evaluate and config.ollama.model is None:
        parser.error("Set ollama.model in configuration or pass --model with an installed model name")
    with ArticleStore(args.database) as store:
        if args.deliver:
            if args.preview:
                result = DeliveryResult()
                for article in store.ready()[:args.limit_items]:
                    try:
                        print(format_message(article), end="\n\n")
                        result.examined += 1
                    except DeliverySetupError as error:
                        logging.error("Cannot preview %s: %s", article.url, error)
                        result.errors += 1
            else:
                result = Delivery(settings, store).deliver_ready(limit=args.limit_items)
            failed = result.errors > 0
        elif args.evaluate:
            result = Evaluator(config, store).evaluate_pending(limit=args.limit_items)
            failed = result.errors > 0
        else:
            result = Collector(config, store).collect(limit_per_source=args.limit_per_source)
            failed = result.source_errors > 0
    operation = ("Preview" if args.preview else "Delivery" if args.deliver
                 else "Evaluation" if args.evaluate else "Collection")
    logging.info("%s result: %s", operation, asdict(result))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Interrupted; committed articles remain saved")
        raise SystemExit(130)
