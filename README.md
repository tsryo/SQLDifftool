# SQL Difftool

Compare the schema of two SQL Server databases (for example DEV and ACC) and browse the differences in a side-by-side diff in your browser.

It covers tables (columns, keys, checks, foreign keys, indexes), views, stored procedures, functions, triggers, user-defined types, sequences, synonyms and schemas. It also compares the database collation and compatibility level.

The tool is read-only: it runs only `SELECT` queries against the system catalog.

## Setup

You need Python 3.10+ and a SQL Server ODBC driver ("ODBC Driver 17 for SQL Server" or 18).

```powershell
python -m virtualenv .venv        # or: python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Run

```powershell
.\sqldifftool.cmd                 # or: .venv\Scripts\python -m sqldifftool
```

A browser opens at http://127.0.0.1:8765. The server listens on this machine only.

1. Fill in the **DEV** and **ACC** cards: server (`host\instance` or `host,port`), database and authentication.
   - Authentication is Windows, SQL login, or a raw ODBC connection string (for example for Entra ID / `ActiveDirectoryInteractive`).
   - **Test connection** checks the login and permissions.
   - **↻** lists the databases on that server.
   - The labels are editable, so you can compare ACC with PRD too.
2. Click **Compare schemas**.
3. Browse the results:
   - The left tree groups objects by type. Filter with the status chips (Different / Only in DEV / Only in ACC / Identical) or the search box.
   - Toggle the normalization options in the top bar. They re-diff instantly without querying the databases again.
   - **Download report** saves a single, self-contained HTML file you can attach to a release ticket. It works offline.

Keyboard: `n`/`p` next/previous object, `/` search, `u` side-by-side ↔ unified, `e` show all lines.

**Save as…** stores a DEV/ACC pair as a profile in `%USERPROFILE%\.sqldifftool\profiles.json`. Passwords are never saved, including `PWD=` inside connection strings.

To try the tool without a database, run `.\sqldifftool.cmd --demo`. It opens a built-in sample comparison.

## Options

| Option | Default | Effect |
|---|---|---|
| Ignore whitespace | on | Indentation, spacing, line breaks and tabs vs spaces don't count. Whitespace inside string literals does. |
| Ignore system-generated constraint names | on | Auto-names like `DF__Orders__Statu__3B75D760` differ between environments. With this on, they are left out of the script. |
| Ignore case | off | Upper/lower case outside string literals doesn't count. |
| Ignore comments | off | `--` and `/* */` comments don't count. |
| Exclude objects | – | Comma-separated wildcards on `schema.name` or `name`, e.g. `staging.*, *_bak`. |

Some normalization is always applied: line endings, trailing whitespace, and `CREATE OR ALTER` → `CREATE`.

## Permissions

The login needs **VIEW DEFINITION** on each database; `db_datareader` alone is not enough.

Without it, SQL Server silently hides objects and definitions, so objects would wrongly show up as "only in" the other environment. The tool detects this and shows a warning.

## How tables are scripted

T-SQL has no built-in "script this table" function, so tables and types are scripted from the catalog views in a stable format:

- Columns stay in their real order.
- Constraints and indexes are sorted by name.
- There is no alignment padding.
- `COLLATE` appears only when a column differs from the database default.
- Disabled or untrusted constraints and disabled indexes/triggers are shown as `ALTER` / `DISABLE` statements.

Storage details are left out on purpose, because they are usually environment-specific: filegroups, partition schemes, data compression and fill factor.

## Known limitations

- **Not compared:** users, roles and permissions; extended properties; full-text indexes; CLR assembly contents; XML schema collections; partition functions/schemes. CLR modules show only their `EXTERNAL NAME`.
- **Renamed modules:** a module renamed with `sp_rename` keeps its old name in `sys.sql_modules`, so its definition can look different from its name.
- **Encrypted modules** (`WITH ENCRYPTION`) cannot be compared. The tool reports them.
- **No sync script:** the tool shows differences but doesn't generate a deployment script.

## Development

```powershell
.venv\Scripts\python -m pytest
```

`tests\sql\seed_difftest.sql` creates `DiffTest_DEV` and `DiffTest_ACC` with known differences, for trying the tool against a real server.
