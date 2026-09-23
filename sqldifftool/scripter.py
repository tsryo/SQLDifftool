"""Turn snapshot objects into deterministic, readable DDL text.

The output is meant for diffing, so it favours stability over completeness:
fixed ordering, no column-aligned padding, and no storage clauses (filegroups,
partition schemes, compression), which are expected to differ per environment.
"""
from __future__ import annotations

from .model import (CheckDef, ColumnDef, DbObject, ForeignKeyDef, IndexColumn, IndexDef,
                    KeyConstraintDef, TableDef)

SYSTEM_NAME_PLACEHOLDER = "[<system-named>]"
INDENT = "    "


def quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def qualified(schema: str, name: str) -> str:
    return f"{quote(schema)}.{quote(name)}" if schema else quote(name)


def format_type(type_name: str, type_schema: str | None, is_user_defined: bool,
                max_length: int, precision: int, scale: int) -> str:
    if is_user_defined:
        return qualified(type_schema or "dbo", type_name)
    t = type_name.lower()
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{t.upper()}({'MAX' if max_length == -1 else max_length})"
    if t in ("nvarchar", "nchar"):
        return f"{t.upper()}({'MAX' if max_length == -1 else max_length // 2})"
    if t in ("decimal", "numeric"):
        return f"{t.upper()}({precision}, {scale})"
    if t in ("datetime2", "time", "datetimeoffset"):
        return f"{t.upper()}({scale})"
    return t.upper()


def script_object(obj: DbObject, db_collation: str, ignore_system_names: bool = True) -> str:
    if obj.table is not None:
        if obj.category == "Table":
            return script_table(obj, db_collation, ignore_system_names)
        return script_table_type(obj, db_collation, ignore_system_names)
    if obj.category in ("View", "Procedure", "Function", "Trigger"):
        return script_module(obj)
    return obj.definition or f"-- {obj.note or 'Definition not available'}"


# --- modules ---------------------------------------------------------------

def script_module(obj: DbObject) -> str:
    parts = []
    if not obj.ansi_nulls:
        parts.append("SET ANSI_NULLS OFF;")
    if not obj.quoted_identifier:
        parts.append("SET QUOTED_IDENTIFIER OFF;")
    if parts:
        parts.append("GO")
    parts.append(obj.definition if obj.definition is not None
                 else f"-- {obj.note or 'Definition not available'}")
    target = qualified(obj.schema, obj.name)
    for ix in sorted(obj.indexes, key=lambda i: i.name.lower()):
        parts.append("GO")
        parts.append(_index_statement(target, ix))
    if obj.category == "Trigger" and obj.disabled:
        on = obj.parent if obj.parent else "DATABASE"
        parts.append("GO")
        parts.append(f"DISABLE TRIGGER {target} ON {on};")
    return "\n".join(parts)


# --- tables ----------------------------------------------------------------

def _constraint_prefix(name: str, system_named: bool, ignore_system_names: bool) -> str:
    if system_named and ignore_system_names:
        return ""
    return f"CONSTRAINT {quote(name)} "


def _constraint_ref(name: str, system_named: bool, ignore_system_names: bool) -> str:
    return SYSTEM_NAME_PLACEHOLDER if system_named and ignore_system_names else quote(name)


def _column(c: ColumnDef, db_collation: str, ign: bool) -> str:
    parts = [quote(c.name)]
    if c.computed is not None:
        parts.append(f"AS {c.computed}")
        if c.persisted:
            parts.append("PERSISTED")
            if not c.nullable:
                parts.append("NOT NULL")
        return " ".join(parts)
    parts.append(c.data_type)
    if c.sparse:
        parts.append("SPARSE")
    if c.collation and c.collation != db_collation:
        parts.append(f"COLLATE {c.collation}")
    if c.generated_always:
        parts.append(f"GENERATED ALWAYS AS {c.generated_always}")
        if c.hidden:
            parts.append("HIDDEN")
    if c.masking_function:
        parts.append(f"MASKED WITH (FUNCTION = '{c.masking_function}')")
    if c.identity:
        parts.append(f"IDENTITY({c.identity})")
    if c.rowguidcol:
        parts.append("ROWGUIDCOL")
    parts.append("NULL" if c.nullable else "NOT NULL")
    if c.default:
        prefix = _constraint_prefix(c.default.name, c.default.system_named, ign)
        parts.append(f"{prefix}DEFAULT {c.default.definition}")
    return " ".join(parts)


def _index_columns(cols: list[IndexColumn]) -> str:
    return ", ".join(f"{quote(c.name)} {'DESC' if c.descending else 'ASC'}" for c in cols)


def _key(k: KeyConstraintDef, ign: bool) -> str:
    kind = "PRIMARY KEY" if k.primary else "UNIQUE"
    prefix = _constraint_prefix(k.name, k.system_named, ign)
    return f"{prefix}{kind} {k.type_desc} ({_index_columns(k.columns)})"


def _check(c: CheckDef, ign: bool) -> str:
    nfr = "NOT FOR REPLICATION " if c.not_for_replication else ""
    return f"{_constraint_prefix(c.name, c.system_named, ign)}CHECK {nfr}{c.definition}"


def _foreign_key(fk: ForeignKeyDef, ign: bool) -> str:
    s = (f"{_constraint_prefix(fk.name, fk.system_named, ign)}FOREIGN KEY "
         f"({', '.join(quote(c) for c in fk.columns)}) REFERENCES {fk.ref_table} "
         f"({', '.join(quote(c) for c in fk.ref_columns)})")
    if fk.on_delete and fk.on_delete != "NO_ACTION":
        s += f" ON DELETE {fk.on_delete.replace('_', ' ')}"
    if fk.on_update and fk.on_update != "NO_ACTION":
        s += f" ON UPDATE {fk.on_update.replace('_', ' ')}"
    if fk.not_for_replication:
        s += " NOT FOR REPLICATION"
    return s


def _sort_key(name: str, system_named: bool, text: str, ign: bool) -> tuple:
    # Hidden system names are not stable, so those constraints sort by content, after named ones.
    return (1, text.lower()) if system_named and ign else (0, name.lower())


def _index_statement(target: str, ix: IndexDef) -> str:
    td = ix.type_desc
    if "COLUMNSTORE" in td:
        cols = "" if td.startswith("CLUSTERED") else f" ({', '.join(quote(c.name) for c in ix.columns)})"
        s = f"CREATE {td} INDEX {quote(ix.name)} ON {target}{cols}"
    elif td in ("XML", "SPATIAL"):
        s = f"CREATE {td} INDEX {quote(ix.name)} ON {target} ({', '.join(quote(c.name) for c in ix.columns)})"
    else:
        unique = "UNIQUE " if ix.unique else ""
        s = f"CREATE {unique}{td} INDEX {quote(ix.name)} ON {target} ({_index_columns(ix.columns)})"
        if ix.included:
            s += f" INCLUDE ({', '.join(quote(c) for c in sorted(ix.included, key=str.lower))})"
        if ix.filter:
            s += f" WHERE {ix.filter}"
        if ix.ignore_dup_key:
            s += " WITH (IGNORE_DUP_KEY = ON)"
    s += ";"
    if ix.disabled:
        s += f"\nALTER INDEX {quote(ix.name)} ON {target} DISABLE;"
    return s


def _body_items(t: TableDef, db_collation: str, ign: bool) -> list[str]:
    items = [_column(c, db_collation, ign) for c in t.columns]
    if t.period:
        items.append(f"PERIOD FOR SYSTEM_TIME ({quote(t.period[0])}, {quote(t.period[1])})")
    keys = sorted(t.keys, key=lambda k: (not k.primary, _sort_key(k.name, k.system_named, _key(k, True), ign)))
    items += [_key(k, ign) for k in keys]
    checks = sorted(t.checks, key=lambda c: _sort_key(c.name, c.system_named, c.definition, ign))
    items += [_check(c, ign) for c in checks]
    fks = sorted(t.foreign_keys, key=lambda f: _sort_key(f.name, f.system_named, _foreign_key(f, True), ign))
    items += [_foreign_key(f, ign) for f in fks]
    return items


def _constraint_state(target: str, t: TableDef, ign: bool) -> list[str]:
    """Statements that reproduce disabled / untrusted CHECK and FOREIGN KEY constraints."""
    out = []
    constraints = [(c, c.definition) for c in t.checks] + [(f, _foreign_key(f, True)) for f in t.foreign_keys]
    for c, text in sorted(constraints, key=lambda x: _sort_key(x[0].name, x[0].system_named, x[1], ign)):
        ref = _constraint_ref(c.name, c.system_named, ign)
        if c.disabled:
            out.append(f"ALTER TABLE {target} NOCHECK CONSTRAINT {ref};")
        elif c.not_trusted and not c.not_for_replication:
            out.append(f"ALTER TABLE {target} WITH NOCHECK CHECK CONSTRAINT {ref};")
    return out


def script_table(obj: DbObject, db_collation: str, ignore_system_names: bool = True) -> str:
    t = obj.table
    target = qualified(obj.schema, obj.name)
    items = _body_items(t, db_collation, ignore_system_names)
    options = []
    if t.memory_optimized:
        options.append("MEMORY_OPTIMIZED = ON")
        if t.durability:
            options.append(f"DURABILITY = {t.durability}")
    if t.history_table:
        options.append(f"SYSTEM_VERSIONING = ON (HISTORY_TABLE = {t.history_table})")
    lines = [f"CREATE TABLE {target} (", ",\n".join(INDENT + i for i in items)]
    lines.append(")" + (f" WITH ({', '.join(options)})" if options else "") + ";")
    lines += _constraint_state(target, t, ignore_system_names)
    lines += [_index_statement(target, ix) for ix in sorted(t.indexes, key=lambda i: i.name.lower())]
    return "\n".join(lines)


def script_table_type(obj: DbObject, db_collation: str, ignore_system_names: bool = True) -> str:
    t = obj.table
    items = _body_items(t, db_collation, ignore_system_names)
    for ix in sorted(t.indexes, key=lambda i: i.name.lower()):
        unique = "UNIQUE " if ix.unique else ""
        items.append(f"INDEX {quote(ix.name)} {unique}{ix.type_desc} ({_index_columns(ix.columns)})")
    tail = ")" + (" WITH (MEMORY_OPTIMIZED = ON)" if t.memory_optimized else "") + ";"
    return "\n".join([f"CREATE TYPE {qualified(obj.schema, obj.name)} AS TABLE (",
                      ",\n".join(INDENT + i for i in items), tail])


# --- simple objects (scripted once, at extraction) -------------------------

def script_schema(name: str, owner: str | None) -> str:
    return f"CREATE SCHEMA {quote(name)}" + (f" AUTHORIZATION {quote(owner)}" if owner else "") + ";"


def script_alias_type(schema: str, name: str, base_type: str, nullable: bool) -> str:
    return f"CREATE TYPE {qualified(schema, name)} FROM {base_type} {'NULL' if nullable else 'NOT NULL'};"


def script_synonym(schema: str, name: str, base_object: str) -> str:
    return f"CREATE SYNONYM {qualified(schema, name)} FOR {base_object};"


def script_sequence(schema: str, name: str, data_type: str, start: str, increment: str,
                    minimum: str, maximum: str, cycling: bool, cached: bool, cache_size: int | None) -> str:
    cache = "NO CACHE" if not cached else (f"CACHE {cache_size}" if cache_size else "CACHE")
    return "\n".join([
        f"CREATE SEQUENCE {qualified(schema, name)}",
        f"{INDENT}AS {data_type}",
        f"{INDENT}START WITH {start}",
        f"{INDENT}INCREMENT BY {increment}",
        f"{INDENT}MINVALUE {minimum}",
        f"{INDENT}MAXVALUE {maximum}",
        f"{INDENT}{'CYCLE' if cycling else 'NO CYCLE'}",
        f"{INDENT}{cache};",
    ])
