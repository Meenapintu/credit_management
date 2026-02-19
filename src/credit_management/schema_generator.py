from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Type

from .models.base import DBSerializableModel
from .models.credits import CreditExpiryRecord, ReservedCredits
from .models.ledger import LedgerEntry
from .models.notification import NotificationEvent
from .models.subscription import SubscriptionPlan, UserSubscription
from .models.transaction import Transaction
from .models.user import UserAccount


MODEL_REGISTRY: List[Type[DBSerializableModel]] = [
    UserAccount,
    Transaction,
    SubscriptionPlan,
    UserSubscription,
    CreditExpiryRecord,
    ReservedCredits,
    NotificationEvent,
    LedgerEntry,
]


def generate_logical_schema() -> Dict[str, Any]:
    """
    Generate a backend-agnostic logical schema for all registered models.
    This is the single source of truth; SQL/NoSQL specific renderers convert it.
    """
    return {model.collection_name: model.db_schema() for model in MODEL_REGISTRY}


def render_sql_ddl(schema: Dict[str, Any], dialect: str = "postgres") -> str:
    """
    Very small SQL DDL renderer. For production you would typically
    plug this into Alembic, Django migrations, or another migration tool.
    """
    lines: List[str] = []
    for table_name, spec in schema.items():
        props = spec["properties"]
        pk = spec.get("primary_key") or "id"
        columns: List[str] = []
        for field_name, meta in props.items():
            sql_type = _map_logical_to_sql(meta["type"], dialect=dialect)
            nullable = "NOT NULL" if field_name in spec.get("required", []) else "NULL"
            columns.append(f'    "{field_name}" {sql_type} {nullable}')
        columns.append(f'    PRIMARY KEY ("{pk}")')
        ddl = f'CREATE TABLE IF NOT EXISTS "{table_name}" (\n' + ",\n".join(columns) + "\n);\n"
        lines.append(ddl)
    return "\n".join(lines)


def render_nosql_schema(schema: Dict[str, Any]) -> str:
    """
    Render a JSON representation that can be used to configure validators
    for document databases like MongoDB.
    """
    return json.dumps(schema, indent=2, default=str)


def _map_logical_to_sql(logical_type: str, dialect: str) -> str:
    logical_type = logical_type.lower()
    if logical_type == "integer":
        return "INTEGER"
    if logical_type == "number":
        return "DOUBLE PRECISION"
    if logical_type == "boolean":
        return "BOOLEAN"
    if logical_type == "string":
        return "TEXT"
    if logical_type in {"datetime", "date"}:
        return "TIMESTAMP"
    return "TEXT"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate DB schemas for the credit management module."
    )
    parser.add_argument(
        "--backend",
        choices=["sql", "nosql"],
        required=True,
        help="Type of schema to generate.",
    )
    parser.add_argument(
        "--dialect",
        default="postgres",
        help="SQL dialect hint (e.g. postgres, mysql).",
    )
    args = parser.parse_args()

    schema = generate_logical_schema()

    if args.backend == "sql":
        ddl = render_sql_ddl(schema, dialect=args.dialect)
        print(ddl)
    else:
        print(render_nosql_schema(schema))


if __name__ == "__main__":
    main()

