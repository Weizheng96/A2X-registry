"""CLI for the registration module.

Usage:
    python -m src.register --config path/to/config.json
    python -m src.register --status
    python -m src.register --status --dataset publicMCP
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from .service import RegistryService

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATABASE_DIR = PROJECT_ROOT / "database"


def main():
    parser = argparse.ArgumentParser(description="A2X Registry - Service Registration CLI")
    parser.add_argument("--config", type=str, help="Path to global config file (user_config.json)")
    parser.add_argument("--status", action="store_true", help="Show registry status")
    parser.add_argument("--dataset", type=str, default=None, help="Filter by dataset name")
    parser.add_argument("--database-dir", type=str, default=str(DATABASE_DIR), help="Database directory")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    database_dir = Path(args.database_dir)
    config_path = Path(args.config) if args.config else None

    service = RegistryService(database_dir, config_path)
    changes = service.startup()

    if args.status:
        status = service.get_status(args.dataset)
        print(json.dumps(status.model_dump(), indent=2, ensure_ascii=False))
    else:
        # Print startup summary
        print(f"\nRegistry startup complete:")
        for ds, state in changes.items():
            count = len(service.list_services(ds))
            print(f"  {ds}: {count} services, taxonomy={state.value}")

        total_status = service.get_status()
        print(f"\nTotal: {total_status.total_services} services across {len(total_status.datasets)} datasets")
        print(f"By source: {json.dumps(total_status.by_source)}")


if __name__ == "__main__":
    main()
