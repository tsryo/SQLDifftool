"""Read a database's catalog into a Snapshot."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from . import catalog_queries as q
from .connection import ConnectionSpec, connect
from .model import (CheckDef, ColumnDef, DbObject, DefaultDef, ForeignKeyDef, IndexColumn, IndexDef,
                    KeyConstraintDef, Snapshot, TableDef)
from .scripter import (format_type, qualified, script_alias_type, script_schema, script_sequence,
                       script_synonym)

MODULE_CATEGORY = {
    "V": "View",
    "P": "Procedure", "PC": "Procedure",
    "FN": "Function", "IF": "Function", "TF": "Function", "FS": "Function", "FT": "Function",
    "TR": "Trigger", "TA": "Trigger",
}

PRODUCT_NAMES = {10: "2008", 11: "2012", 12: "2014", 13: "2016", 14: "2017", 15: "2019", 16: "2022", 17: "2025"}


def _rows(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def describe_version(info: dict) -> str:
    version = info.get("product_version") or ""
    engine = info.get("engine_edition")
    if engine == 5:
        return f"Azure SQL Database ({version})"
    if engine == 8:
        return f"Azure SQL Managed Instance ({version})"
    try:
        major = int(version.split(".")[0])
    except ValueError:
        return version
    return f"SQL Server {PRODUCT_NAMES.get(major, major)} ({version})"


def _permission_warnings(info: dict) -> list[dict]:
    if info.get("can_view_definition"):
        return []
    return [{"level": "warn", "text": (
        f"The login has no VIEW DEFINITION permission on {info.get('database_name')}. "
        "SQL Server hides objects and definitions it cannot show, so objects may wrongly "
        "appear as missing on this side.")}]


def probe(spec: ConnectionSpec) -> dict:
    """Quick connection test: server info and object count."""
    conn = connect(spec)
    try:
        cur = conn.cursor()
        info = _rows(cur, q.SERVER_INFO)[0]
        count = cur.execute(q.OBJECT_COUNT).fetchone()[0]
    finally:
        conn.close()
    return {
        "server": info["server_name"],
        "database": info["database_name"],
        "version": describe_version(info),
        "edition": info["edition"],
        "collation": info["collation"],
        "compatLevel": info["compat_level"],
        "objectCount": count,
        "warnings": _permission_warnings(info),
    }


def list_databases(spec: ConnectionSpec) -> list[str]:
    conn = connect(spec, include_database=False)
    try:
        return [r[0] for r in conn.cursor().execute(q.DATABASES).fetchall()]
    finally:
        conn.close()


def extract_snapshot(spec: ConnectionSpec) -> Snapshot:
    conn = connect(spec)
    try:
        return _extract(conn.cursor(), spec)
    finally:
        conn.close()


def _extract(cur, spec: ConnectionSpec) -> Snapshot:
    info = _rows(cur, q.SERVER_INFO)[0]
    features = q.Features.from_probe(_rows(cur, q.FEATURE_PROBE))
    snap = Snapshot(
        label=spec.label,
        server=spec.server or info["server_name"] or "",
        database=info["database_name"],
        server_version=describe_version(info),
        edition=info["edition"] or "",
        collation=info["collation"] or "",
        compat_level=info["compat_level"],
        extracted_at=datetime.now().isoformat(timespec="seconds"),
        warnings=_permission_warnings(info),
    )

    owners: dict[int, TableDef] = {}
    for r in _rows(cur, q.tables(features)):
        t = TableDef(memory_optimized=bool(r["is_memory_optimized"]),
                     durability=r["durability_desc"] if r["is_memory_optimized"] else None)
        if r["temporal_type"] == 2 and r["history_name"]:
            t.history_table = qualified(r["history_schema"], r["history_name"])
        owners[r["object_id"]] = t
        snap.objects.append(DbObject("Table", r["schema_name"], r["name"], table=t))
    for r in _rows(cur, q.table_types(features)):
        t = TableDef(memory_optimized=bool(r["is_memory_optimized"]))
        owners[r["object_id"]] = t
        snap.objects.append(DbObject("Type", r["schema_name"], r["name"], table=t))

    for r in _rows(cur, q.columns(features)):
        t = owners.get(r["object_id"])
        if t is not None:
            t.columns.append(_column(r))

    views: dict[int, DbObject] = {}
    encrypted = 0
    for r in _rows(cur, q.MODULES):
        obj = _module(r)
        if r["is_encrypted"] and r["definition"] is None:
            encrypted += 1
        if r["type"] == "V":
            views[r["object_id"]] = obj
        snap.objects.append(obj)
    for r in _rows(cur, q.DATABASE_TRIGGERS):
        snap.objects.append(DbObject(
            "Trigger", "", r["name"], parent="DATABASE", definition=r["definition"],
            ansi_nulls=r["uses_ansi_nulls"] is not False, quoted_identifier=r["uses_quoted_identifier"] is not False,
            disabled=bool(r["is_disabled"]), note="" if r["definition"] is not None else "Definition not available"))

    _attach_indexes(cur, owners, views)
    _attach_checks(cur, owners)
    _attach_foreign_keys(cur, owners)
    if features.temporal:
        for r in _rows(cur, q.PERIODS):
            if r["object_id"] in owners:
                owners[r["object_id"]].period = [r["start_column"], r["end_column"]]

    _add_simple_objects(cur, snap)
    if encrypted:
        snap.warnings.append({"level": "info", "text": (
            f"{encrypted} module(s) are encrypted (WITH ENCRYPTION); their definitions cannot be compared.")})
    return snap


def _column(r: dict) -> ColumnDef:
    c = ColumnDef(
        name=r["column_name"],
        data_type=format_type(r["type_name"], r["type_schema"], bool(r["is_user_defined"]),
                              r["max_length"], r["precision"], r["scale"]),
        nullable=bool(r["is_nullable"]),
        collation=r["collation_name"],
        rowguidcol=bool(r["is_rowguidcol"]),
        sparse=bool(r["is_sparse"]),
        hidden=bool(r["is_hidden"]),
        masking_function=r["masking_function"],
    )
    if r["is_identity"]:
        c.identity = f"{r['identity_seed']}, {r['identity_increment']}"
    if r["is_computed"]:
        c.computed = r["computed_definition"]
        c.persisted = bool(r["is_persisted"])
    if r["default_name"]:
        c.default = DefaultDef(r["default_name"], r["default_definition"], bool(r["default_system_named"]))
    if r["generated_always_type"] in (1, 2):
        c.generated_always = "ROW START" if r["generated_always_type"] == 1 else "ROW END"
    return c


def _module(r: dict) -> DbObject:
    obj = DbObject(
        MODULE_CATEGORY[r["type"]], r["schema_name"], r["name"],
        definition=r["definition"],
        ansi_nulls=r["uses_ansi_nulls"] is not False,
        quoted_identifier=r["uses_quoted_identifier"] is not False,
        disabled=bool(r["trigger_disabled"]),
    )
    if r["parent_name"]:
        obj.parent = qualified(r["parent_schema"], r["parent_name"])
    if r["assembly_name"]:
        obj.definition = (f"-- CLR {obj.category.lower()}: EXTERNAL NAME "
                          f"[{r['assembly_name']}].[{r['assembly_class']}].[{r['assembly_method']}]")
    elif r["definition"] is None:
        obj.note = ("Definition is encrypted (WITH ENCRYPTION)" if r["is_encrypted"]
                    else "Definition not available (check VIEW DEFINITION permission)")
    return obj


def _attach_indexes(cur, owners: dict[int, TableDef], views: dict[int, DbObject]) -> None:
    cols: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in _rows(cur, q.INDEX_COLUMNS):
        cols[(r["object_id"], r["index_id"])].append(r)

    for r in _rows(cur, q.INDEXES):
        oid = r["object_id"]
        if oid not in owners and oid not in views:
            continue
        rows = cols[(oid, r["index_id"])]
        keys = sorted((c for c in rows if c["key_ordinal"] > 0), key=lambda c: c["key_ordinal"])
        key_cols = [IndexColumn(c["column_name"], bool(c["is_descending_key"])) for c in keys]
        if r["is_primary_key"] or r["is_unique_constraint"]:
            owners[oid].keys.append(KeyConstraintDef(
                r["name"], bool(r["is_primary_key"]), r["type_desc"], key_cols,
                bool(r["constraint_system_named"])))
            continue
        if "COLUMNSTORE" in r["type_desc"]:
            key_cols = [IndexColumn(c["column_name"]) for c in sorted(rows, key=lambda c: c["index_column_id"])
                        if c["key_ordinal"] > 0 or c["is_included_column"] or not c["partition_ordinal"]]
        ix = IndexDef(
            name=r["name"], type_desc=r["type_desc"], unique=bool(r["is_unique"]), columns=key_cols,
            included=[c["column_name"] for c in rows if c["is_included_column"] and "COLUMNSTORE" not in r["type_desc"]],
            filter=r["filter_definition"] if r["has_filter"] else None,
            disabled=bool(r["is_disabled"]), ignore_dup_key=bool(r["ignore_dup_key"]))
        if oid in owners:
            owners[oid].indexes.append(ix)
        else:
            views[oid].indexes.append(ix)


def _attach_checks(cur, owners: dict[int, TableDef]) -> None:
    for r in _rows(cur, q.CHECKS):
        t = owners.get(r["object_id"])
        if t is not None:
            t.checks.append(CheckDef(r["name"], r["definition"], bool(r["is_system_named"]), bool(r["is_disabled"]),
                                     bool(r["is_not_trusted"]), bool(r["is_not_for_replication"])))


def _attach_foreign_keys(cur, owners: dict[int, TableDef]) -> None:
    cols: dict[int, list[dict]] = defaultdict(list)
    for r in _rows(cur, q.FOREIGN_KEY_COLUMNS):
        cols[r["fk_id"]].append(r)
    for r in _rows(cur, q.FOREIGN_KEYS):
        t = owners.get(r["object_id"])
        if t is None:
            continue
        fk_cols = sorted(cols[r["fk_id"]], key=lambda c: c["constraint_column_id"])
        t.foreign_keys.append(ForeignKeyDef(
            name=r["name"],
            columns=[c["column_name"] for c in fk_cols],
            ref_table=qualified(r["ref_schema"], r["ref_table"]),
            ref_columns=[c["ref_column_name"] for c in fk_cols],
            on_delete=r["delete_referential_action_desc"],
            on_update=r["update_referential_action_desc"],
            system_named=bool(r["is_system_named"]),
            disabled=bool(r["is_disabled"]),
            not_trusted=bool(r["is_not_trusted"]),
            not_for_replication=bool(r["is_not_for_replication"]),
        ))


def _add_simple_objects(cur, snap: Snapshot) -> None:
    for r in _rows(cur, q.SCHEMAS):
        snap.objects.append(DbObject("Schema", "", r["name"], definition=script_schema(r["name"], r["owner"])))
    for r in _rows(cur, q.ALIAS_TYPES):
        base = format_type(r["base_type"], None, False, r["max_length"], r["precision"], r["scale"])
        snap.objects.append(DbObject("Type", r["schema_name"], r["name"], definition=script_alias_type(
            r["schema_name"], r["name"], base, bool(r["is_nullable"]))))
    for r in _rows(cur, q.SEQUENCES):
        user_type = r["type_name"] != r["system_type_name"]
        data_type = format_type(r["type_name"], "dbo", user_type, 0, r["precision"], r["scale"])
        snap.objects.append(DbObject("Sequence", r["schema_name"], r["name"], definition=script_sequence(
            r["schema_name"], r["name"], data_type, r["start_value"], r["increment"], r["minimum_value"],
            r["maximum_value"], bool(r["is_cycling"]), bool(r["is_cached"]), r["cache_size"])))
    for r in _rows(cur, q.SYNONYMS):
        snap.objects.append(DbObject("Synonym", r["schema_name"], r["name"], definition=script_synonym(
            r["schema_name"], r["name"], r["base_object_name"])))
