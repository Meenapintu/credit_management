from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Type

from pydantic import BaseModel, Field


class DBSerializableModel(BaseModel):
    """
    Base Pydantic model that knows how to:
    - Serialize itself for DB persistence
    - Provide a backend-agnostic DB schema description derived from fields

    The actual SQL/NoSQL DDL is produced offline by the schema generator
    using this description; this class is not meant to hit the database
    at runtime for schema work.
    """

    # Logical collection / table name; subclasses should override
    collection_name: ClassVar[str]

    # Optional explicit primary key field; defaults to "id" if present
    primary_key: ClassVar[Optional[str]] = "id"

    def serialize_for_db(self) -> Dict[str, Any]:
        """
        Convert to a dict suitable for DB persistence.

        This is the single place to control how models are stored;
        DB adapters can still post-process this if needed.
        """
        return self.model_dump(by_alias=True, exclude_none=True)

    @classmethod
    def db_schema(cls) -> Dict[str, Any]:
        """
        Return a backend-agnostic schema description derived from model fields.

        The schema generator runs this once (e.g. from a CLI) to produce:
        - SQL DDL for relational databases
        - JSON/metadata for NoSQL collections and indexes
        """
        fields: Mapping[str, Any] = cls.model_fields

        properties: Dict[str, Any] = {}
        required: list[str] = []

        for name, field in fields.items():
            if name == "collection_name":
                continue

            # Basic JSON-style type mapping; the generator can refine this
            field_type = cls._map_type(field.annotation)

            properties[name] = {
                "type": field_type,
                "nullable": field.is_required() is False and not field.serialization_alias,
                "default": field.default if field.default is not None else None,
                "description": field.description,
            }

            if field.is_required():
                required.append(name)

        return {
            "collection_name": cls.collection_name,
            "primary_key": cls.primary_key,
            "properties": properties,
            "required": required,
        }

    @staticmethod
    def _map_type(annotation: Any) -> str:
        """
        Map a Python / Pydantic type annotation to a generic logical type.
        The schema generator will translate these to dialect-specific types.
        """
        origin: Any = getattr(annotation, "__origin__", None)
        if origin is list or origin is tuple or origin is set:
            return "array"

        if annotation in (int,):
            return "integer"
        if annotation in (float,):
            return "number"
        if annotation in (bool,):
            return "boolean"
        if annotation in (str,):
            return "string"

        # Fallback for datetime, UUID, etc.; generator can refine using metadata
        name = getattr(annotation, "__name__", "object")
        return name.lower()


class PaginatedResult(BaseModel):
    items: list[Any] = Field(default_factory=list)
    total: int
    limit: int
    offset: int

