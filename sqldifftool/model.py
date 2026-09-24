"""Snapshot data model: what is extracted from a database, before it is scripted.

Tables and table types are kept structured (not as text) so that scripting options,
such as hiding system-generated constraint names, can be applied at compare time
without querying the database again.
"""
from __future__ import annotations

import types
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Union, get_args, get_origin, get_type_hints

# Display order of object categories.
CATEGORIES = ("Table", "View", "Procedure", "Function", "Trigger", "Type", "Sequence", "Synonym", "Schema")
MODULE_CATEGORIES = ("View", "Procedure", "Function", "Trigger")


@dataclass
class DefaultDef:
    name: str
    definition: str
    system_named: bool = False


@dataclass
class ColumnDef:
    name: str
    data_type: str = ""
    nullable: bool = True
    collation: str | None = None
    identity: str | None = None          # "seed, increment"
    computed: str | None = None          # computed column expression
    persisted: bool = False
    default: DefaultDef | None = None
    rowguidcol: bool = False
    sparse: bool = False
    generated_always: str | None = None  # "ROW START" / "ROW END" (temporal tables)
    hidden: bool = False
    masking_function: str | None = None


@dataclass
class IndexColumn:
    name: str
    descending: bool = False


@dataclass
class KeyConstraintDef:
    """PRIMARY KEY or UNIQUE constraint."""
    name: str
    primary: bool
    type_desc: str = "CLUSTERED"         # CLUSTERED / NONCLUSTERED / NONCLUSTERED HASH
    columns: list[IndexColumn] = field(default_factory=list)
    system_named: bool = False


@dataclass
class CheckDef:
    name: str
    definition: str
    system_named: bool = False
    disabled: bool = False
    not_trusted: bool = False
    not_for_replication: bool = False


@dataclass
class ForeignKeyDef:
    name: str
    columns: list[str]
    ref_table: str                       # already quoted, e.g. [dbo].[Customers]
    ref_columns: list[str]
    on_delete: str = "NO_ACTION"
    on_update: str = "NO_ACTION"
    system_named: bool = False
    disabled: bool = False
    not_trusted: bool = False
    not_for_replication: bool = False


@dataclass
class IndexDef:
    """An index that is not backing a PRIMARY KEY / UNIQUE constraint."""
    name: str
    type_desc: str = "NONCLUSTERED"
    unique: bool = False
    columns: list[IndexColumn] = field(default_factory=list)
    included: list[str] = field(default_factory=list)
    filter: str | None = None
    disabled: bool = False
    ignore_dup_key: bool = False


@dataclass
class TableDef:
    columns: list[ColumnDef] = field(default_factory=list)
    keys: list[KeyConstraintDef] = field(default_factory=list)
    checks: list[CheckDef] = field(default_factory=list)
    foreign_keys: list[ForeignKeyDef] = field(default_factory=list)
    indexes: list[IndexDef] = field(default_factory=list)
    memory_optimized: bool = False
    durability: str | None = None
    period: list[str] | None = None      # [start column, end column]
    history_table: str | None = None     # already quoted


@dataclass
class DbObject:
    category: str
    schema: str
    name: str
    parent: str = ""                     # triggers: quoted parent table, or "DATABASE"
    definition: str | None = None        # module source, or pre-scripted DDL for simple objects
    ansi_nulls: bool = True
    quoted_identifier: bool = True
    disabled: bool = False
    note: str = ""                       # e.g. why a definition is unavailable
    table: TableDef | None = None        # tables and table types
    indexes: list[IndexDef] = field(default_factory=list)  # indexed views
    attached: list[str] = field(default_factory=list)      # script files: constraint/index statements

    @property
    def key(self) -> str:
        return f"{self.category}|{self.schema}|{self.name}".lower()

    @property
    def display_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


@dataclass
class Snapshot:
    label: str
    server: str
    database: str
    server_version: str = ""
    edition: str = ""
    collation: str = ""
    compat_level: int | None = None
    extracted_at: str = ""
    warnings: list[dict] = field(default_factory=list)  # {"level": "warn"|"info", "text": ...}
    objects: list[DbObject] = field(default_factory=list)
    source: str = "database"             # database | scripts

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        return _build(cls, data)


def _build(cls: type, data: dict | None) -> Any:
    if data is None:
        return None
    hints = get_type_hints(cls)
    kwargs = {f.name: _convert(hints[f.name], data[f.name]) for f in fields(cls) if f.name in data}
    return cls(**kwargs)


def _convert(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        inner = [a for a in get_args(tp) if a is not type(None)]
        return _convert(inner[0], value)
    if origin is list:
        (item_type,) = get_args(tp)
        return [_convert(item_type, v) for v in value]
    if is_dataclass(tp):
        return _build(tp, value)
    return value
