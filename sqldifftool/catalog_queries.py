"""Catalog queries. Each returns all rows of one kind for the whole database (no per-object queries).

sql_variant values (identity seed, sequence values) are cast to nvarchar because
pyodbc cannot read sql_variant.
"""
from __future__ import annotations

from dataclasses import dataclass

SERVER_INFO = """
SELECT
    CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
    CAST(SERVERPROPERTY('Edition') AS nvarchar(128))        AS edition,
    CAST(SERVERPROPERTY('EngineEdition') AS int)            AS engine_edition,
    CAST(@@SERVERNAME AS nvarchar(256))                     AS server_name,
    DB_NAME()                                               AS database_name,
    CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS nvarchar(128)) AS collation,
    (SELECT compatibility_level FROM sys.databases WHERE database_id = DB_ID()) AS compat_level,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION') AS can_view_definition
"""

OBJECT_COUNT = """
SELECT COUNT(*) FROM sys.objects
WHERE is_ms_shipped = 0 AND type IN ('U', 'V', 'P', 'PC', 'FN', 'IF', 'TF', 'FS', 'FT', 'TR', 'TA', 'SO', 'SN')
"""

DATABASES = """
SELECT name FROM sys.databases
WHERE state = 0 AND database_id > 4 AND HAS_DBACCESS(name) = 1
ORDER BY name
"""

# Which version-dependent catalog columns exist on this server.
FEATURE_PROBE = """
SELECT OBJECT_NAME(object_id) AS view_name, name AS column_name
FROM sys.all_columns
WHERE object_id IN (OBJECT_ID('sys.tables'), OBJECT_ID('sys.columns'), OBJECT_ID('sys.table_types'))
UNION ALL
SELECT 'views', name FROM sys.all_views WHERE name IN ('periods', 'masked_columns') AND schema_id = SCHEMA_ID('sys')
"""


@dataclass(frozen=True)
class Features:
    memory_optimized: bool = False
    temporal: bool = False
    hidden_columns: bool = False
    masking: bool = False

    @classmethod
    def from_probe(cls, rows: list[dict]) -> Features:
        have = {(r["view_name"], r["column_name"]) for r in rows}
        return cls(
            memory_optimized=("tables", "is_memory_optimized") in have,
            temporal=("tables", "temporal_type") in have and ("views", "periods") in have,
            hidden_columns=("columns", "is_hidden") in have,
            masking=("views", "masked_columns") in have,
        )


# Excludes objects that SSMS tooling marks as its own (database diagrams).
NOT_TOOLS_OBJECT = """NOT EXISTS (
        SELECT 1 FROM sys.extended_properties ep
        WHERE ep.class = 1 AND ep.major_id = {alias}.object_id AND ep.minor_id = 0
          AND ep.name = N'microsoft_database_tools_support')"""

# Object ids whose columns, constraints and indexes are extracted.
OWNER_IDS = """(
        SELECT object_id FROM sys.tables WHERE is_ms_shipped = 0
        UNION ALL SELECT object_id FROM sys.views WHERE is_ms_shipped = 0
        UNION ALL SELECT type_table_object_id FROM sys.table_types WHERE is_user_defined = 1)"""


def tables(f: Features) -> str:
    mem = "t.is_memory_optimized, t.durability_desc" if f.memory_optimized else \
        "CAST(0 AS bit) AS is_memory_optimized, CAST(NULL AS nvarchar(60)) AS durability_desc"
    temporal = ("t.temporal_type, OBJECT_SCHEMA_NAME(t.history_table_id) AS history_schema, "
                "OBJECT_NAME(t.history_table_id) AS history_name") if f.temporal else \
        "CAST(0 AS tinyint) AS temporal_type, CAST(NULL AS sysname) AS history_schema, CAST(NULL AS sysname) AS history_name"
    return f"""
SELECT t.object_id, SCHEMA_NAME(t.schema_id) AS schema_name, t.name, {mem}, {temporal}
FROM sys.tables t
WHERE t.is_ms_shipped = 0 AND {NOT_TOOLS_OBJECT.format(alias='t')}
"""


def table_types(f: Features) -> str:
    mem = "tt.is_memory_optimized" if f.memory_optimized else "CAST(0 AS bit) AS is_memory_optimized"
    return f"""
SELECT tt.type_table_object_id AS object_id, SCHEMA_NAME(tt.schema_id) AS schema_name, tt.name, {mem}
FROM sys.table_types tt
WHERE tt.is_user_defined = 1
"""


def columns(f: Features) -> str:
    temporal = "c.generated_always_type" if f.temporal else "CAST(0 AS tinyint) AS generated_always_type"
    hidden = "c.is_hidden" if f.hidden_columns else "CAST(0 AS bit) AS is_hidden"
    mask_col = "mc.masking_function" if f.masking else "CAST(NULL AS nvarchar(4000)) AS masking_function"
    mask_join = ("LEFT JOIN sys.masked_columns mc ON mc.object_id = c.object_id AND mc.column_id = c.column_id"
                 if f.masking else "")
    return f"""
SELECT c.object_id, c.column_id, c.name AS column_name,
       ty.name AS type_name, SCHEMA_NAME(ty.schema_id) AS type_schema, ty.is_user_defined,
       c.max_length, c.precision, c.scale, c.is_nullable, c.collation_name,
       c.is_identity,
       CAST(ic.seed_value AS nvarchar(60)) AS identity_seed,
       CAST(ic.increment_value AS nvarchar(60)) AS identity_increment,
       c.is_computed, cc.definition AS computed_definition, cc.is_persisted,
       dc.name AS default_name, dc.definition AS default_definition, dc.is_system_named AS default_system_named,
       c.is_rowguidcol, c.is_sparse, {temporal}, {hidden}, {mask_col}
FROM sys.columns c
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.identity_columns ic ON ic.object_id = c.object_id AND ic.column_id = c.column_id
LEFT JOIN sys.computed_columns cc ON cc.object_id = c.object_id AND cc.column_id = c.column_id
LEFT JOIN sys.default_constraints dc ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
{mask_join}
WHERE c.object_id IN {OWNER_IDS}
ORDER BY c.object_id, c.column_id
"""


INDEXES = f"""
SELECT i.object_id, i.index_id, i.name, i.type_desc, i.is_unique, i.is_primary_key, i.is_unique_constraint,
       i.is_disabled, i.ignore_dup_key, i.has_filter, i.filter_definition,
       kc.is_system_named AS constraint_system_named
FROM sys.indexes i
LEFT JOIN sys.key_constraints kc
       ON kc.parent_object_id = i.object_id AND kc.unique_index_id = i.index_id AND kc.type IN ('PK', 'UQ')
WHERE i.index_id > 0 AND i.is_hypothetical = 0 AND i.object_id IN {OWNER_IDS}
"""

INDEX_COLUMNS = f"""
SELECT ic.object_id, ic.index_id, ic.index_column_id, ic.key_ordinal, ic.partition_ordinal,
       ic.is_descending_key, ic.is_included_column, c.name AS column_name
FROM sys.index_columns ic
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE ic.object_id IN {OWNER_IDS}
ORDER BY ic.object_id, ic.index_id, ic.key_ordinal, ic.index_column_id
"""

CHECKS = f"""
SELECT cc.parent_object_id AS object_id, cc.name, cc.definition, cc.is_system_named,
       cc.is_disabled, cc.is_not_trusted, cc.is_not_for_replication
FROM sys.check_constraints cc
WHERE cc.parent_object_id IN {OWNER_IDS}
"""

FOREIGN_KEYS = f"""
SELECT fk.object_id AS fk_id, fk.parent_object_id AS object_id, fk.name, fk.is_system_named,
       fk.is_disabled, fk.is_not_trusted, fk.is_not_for_replication,
       fk.delete_referential_action_desc, fk.update_referential_action_desc,
       OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS ref_schema, OBJECT_NAME(fk.referenced_object_id) AS ref_table
FROM sys.foreign_keys fk
WHERE fk.parent_object_id IN {OWNER_IDS}
"""

FOREIGN_KEY_COLUMNS = """
SELECT fkc.constraint_object_id AS fk_id, fkc.constraint_column_id,
       pc.name AS column_name, rc.name AS ref_column_name
FROM sys.foreign_key_columns fkc
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
ORDER BY fkc.constraint_object_id, fkc.constraint_column_id
"""

PERIODS = """
SELECT p.object_id, sc.name AS start_column, ec.name AS end_column
FROM sys.periods p
JOIN sys.columns sc ON sc.object_id = p.object_id AND sc.column_id = p.start_column_id
JOIN sys.columns ec ON ec.object_id = p.object_id AND ec.column_id = p.end_column_id
"""

MODULES = f"""
SELECT o.object_id, SCHEMA_NAME(o.schema_id) AS schema_name, o.name, RTRIM(o.type) AS type,
       m.definition, m.uses_ansi_nulls, m.uses_quoted_identifier,
       OBJECTPROPERTY(o.object_id, 'IsEncrypted') AS is_encrypted,
       OBJECT_SCHEMA_NAME(o.parent_object_id) AS parent_schema, OBJECT_NAME(o.parent_object_id) AS parent_name,
       tr.is_disabled AS trigger_disabled,
       a.name AS assembly_name, am.assembly_class, am.assembly_method
FROM sys.objects o
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
LEFT JOIN sys.triggers tr ON tr.object_id = o.object_id
LEFT JOIN sys.assembly_modules am ON am.object_id = o.object_id
LEFT JOIN sys.assemblies a ON a.assembly_id = am.assembly_id
WHERE o.is_ms_shipped = 0
  AND o.type IN ('V', 'P', 'PC', 'FN', 'IF', 'TF', 'FS', 'FT', 'TR', 'TA')
  AND {NOT_TOOLS_OBJECT.format(alias='o')}
"""

DATABASE_TRIGGERS = """
SELECT tr.name, tr.is_disabled, m.definition, m.uses_ansi_nulls, m.uses_quoted_identifier
FROM sys.triggers tr
LEFT JOIN sys.sql_modules m ON m.object_id = tr.object_id
WHERE tr.parent_class = 0 AND tr.is_ms_shipped = 0
"""

SCHEMAS = """
SELECT s.name, USER_NAME(s.principal_id) AS owner
FROM sys.schemas s
WHERE s.schema_id BETWEEN 5 AND 16383
"""

ALIAS_TYPES = """
SELECT SCHEMA_NAME(t.schema_id) AS schema_name, t.name, bt.name AS base_type,
       t.max_length, t.precision, t.scale, t.is_nullable
FROM sys.types t
JOIN sys.types bt ON bt.user_type_id = t.system_type_id
WHERE t.is_user_defined = 1 AND t.is_table_type = 0 AND t.is_assembly_type = 0
"""

SEQUENCES = """
SELECT SCHEMA_NAME(s.schema_id) AS schema_name, s.name,
       TYPE_NAME(s.user_type_id) AS type_name, TYPE_NAME(s.system_type_id) AS system_type_name,
       s.precision, s.scale,
       CAST(s.start_value AS nvarchar(60)) AS start_value,
       CAST(s.increment AS nvarchar(60)) AS increment,
       CAST(s.minimum_value AS nvarchar(60)) AS minimum_value,
       CAST(s.maximum_value AS nvarchar(60)) AS maximum_value,
       s.is_cycling, s.is_cached, s.cache_size
FROM sys.sequences s
WHERE s.is_ms_shipped = 0
"""

SYNONYMS = """
SELECT SCHEMA_NAME(s.schema_id) AS schema_name, s.name, s.base_object_name
FROM sys.synonyms s
WHERE s.is_ms_shipped = 0
"""
