"""Read exported DDL scripts (.sql files) into a Snapshot, for comparing without a database connection.

Understands the output of SSMS "Generate Scripts" (one file or one file per object),
SSDT projects and similar exports. Tables are kept as text: their constraint and
index statements are attached to them, and storage clauses (filegroups, fill
factor, compression) are stripped because they differ per environment.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from .model import DbObject, Snapshot
from .normalize import BLOCK_COMMENT, LINE_COMMENT, QUOTED_ID, STRING, SYMBOL, WORD, WS, tokenize
from .scripter import qualified

TRIVIA = (WS, LINE_COMMENT, BLOCK_COMMENT)
MODULE_WORDS = {"VIEW": "View", "PROC": "Procedure", "PROCEDURE": "Procedure", "FUNCTION": "Function",
                "TRIGGER": "Trigger"}
SIMPLE_WORDS = {"TABLE": "Table", "TYPE": "Type", "SEQUENCE": "Sequence", "SYNONYM": "Synonym", "SCHEMA": "Schema"}
ALTER_TARGETS = {"TABLE", "VIEW", "PROC", "PROCEDURE", "FUNCTION", "TRIGGER", "DATABASE", "INDEX", "SCHEMA",
                 "SEQUENCE", "AUTHORIZATION", "ROLE", "USER", "LOGIN", "QUEUE", "ASSEMBLY"}
SESSION_OPTIONS = {"ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_PADDING", "ANSI_WARNINGS", "ARITHABORT",
                   "CONCAT_NULL_YIELDS_NULL", "NUMERIC_ROUNDABORT", "NOCOUNT", "XACT_ABORT", "NOEXEC",
                   "IDENTITY_INSERT", "ANSI_NULL_DFLT_ON", "DATEFORMAT", "TRANSACTION", "LOCK_TIMEOUT"}
# WITH (...) options that are storage or build settings, not schema.
STORAGE_OPTIONS = {"PAD_INDEX", "STATISTICS_NORECOMPUTE", "SORT_IN_TEMPDB", "ONLINE", "ALLOW_ROW_LOCKS",
                   "ALLOW_PAGE_LOCKS", "OPTIMIZE_FOR_SEQUENTIAL_KEY", "FILLFACTOR", "DATA_COMPRESSION",
                   "XML_COMPRESSION", "DROP_EXISTING", "STATISTICS_INCREMENTAL", "MAXDOP", "RESUMABLE",
                   "MAX_DURATION"}
MAX_EXAMPLES = 5


def decode(data: bytes) -> str:
    """Decode a script file; SSMS saves UTF-16 with a BOM by default."""
    for bom, encoding in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                          (codecs.BOM_UTF16_BE, "utf-16-be")):
        if data.startswith(bom):
            return data[len(bom):].decode(encoding, errors="replace")
    if len(data) >= 4 and data[1::2].count(0) > len(data) // 4:  # UTF-16 LE without a BOM
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# --- lexing helpers ----------------------------------------------------------

@dataclass
class Tok:
    kind: str
    text: str
    offset: int

    @property
    def up(self) -> str:
        return self.text.upper() if self.kind == WORD else ""


def _tokens(text: str) -> list[Tok]:
    out, pos = [], 0
    for kind, tok in tokenize(text):
        out.append(Tok(kind, tok, pos))
        pos += len(tok)
    return out


def _significant(toks: list[Tok]) -> list[Tok]:
    return [t for t in toks if t.kind not in TRIVIA]


def _unquote(tok: Tok) -> str:
    if tok.kind == QUOTED_ID:
        close = "]" if tok.text[0] == "[" else '"'
        return tok.text[1:-1].replace(close * 2, close)
    return tok.text


def _read_name(sig: list[Tok], i: int) -> tuple[list[str], int]:
    """Read a dotted multi-part name starting at sig[i]; returns its parts and the index after it."""
    parts: list[str] = []
    while i < len(sig) and sig[i].kind in (WORD, QUOTED_ID):
        parts.append(_unquote(sig[i]))
        i += 1
        if i < len(sig) and sig[i].kind == SYMBOL and sig[i].text == ".":
            i += 1
        else:
            break
    return parts, i


def _schema_name(parts: list[str], default_schema: str = "dbo") -> tuple[str, str]:
    if not parts:
        return "", ""
    return (parts[-2] if len(parts) >= 2 else default_schema), parts[-1]


def split_batches(text: str) -> list[str]:
    """Split a script on GO lines. A GO inside a string or comment does not count."""
    toks = _tokens(text)

    def at_line_start(i: int) -> bool:
        if i == 0:
            return True
        prev = toks[i - 1]
        return prev.kind == WS and ("\n" in prev.text or i == 1)

    batches, start, i, n = [], 0, 0, len(toks)
    while i < n:
        if toks[i].up == "GO" and at_line_start(i):
            j = i + 1
            if j < n and toks[j].kind == WS and "\n" not in toks[j].text:
                j += 1
            if j < n and toks[j].kind == WORD and toks[j].text.isdigit():
                j += 1
                if j < n and toks[j].kind == WS and "\n" not in toks[j].text:
                    j += 1
            if j < n and toks[j].kind == LINE_COMMENT:
                j += 1
            if j == n or (toks[j].kind == WS and "\n" in toks[j].text):
                batches.append(text[start:toks[i].offset])
                start = toks[j].offset if j < n else len(text)
                i = j
                continue
        i += 1
    batches.append(text[start:])
    return [b for b in batches if _significant(_tokens(b))]


def _is_module_start(sig: list[Tok], i: int) -> bool:
    if sig[i].up not in ("CREATE", "ALTER"):
        return False
    j = i + 1
    if sig[i].up == "CREATE" and j + 1 < len(sig) and sig[j].up == "OR" and sig[j + 1].up == "ALTER":
        j += 2
    return j < len(sig) and sig[j].up in MODULE_WORDS


def _is_boundary(sig: list[Tok], i: int) -> bool:
    """Does sig[i] start a new statement in a batch without semicolons?"""
    word = sig[i].up
    prev = sig[i - 1].up if i else ""
    nxt = sig[i + 1].up if i + 1 < len(sig) else ""
    if word == "CREATE":
        return True
    if word == "ALTER":
        return prev != "OR" and nxt in ALTER_TARGETS
    if word in ("USE", "PRINT"):
        return True
    if word in ("GRANT", "DENY", "REVOKE"):
        return prev != "WITH"
    if word == "INSERT":
        return prev not in ("GRANT", "DENY", "REVOKE", "OF", "FOR", "AFTER") and sig[i - 1].text != ","
    if word == "SET":
        return nxt in SESSION_OPTIONS
    return False


def split_statements(batch: str) -> list[str]:
    """Split a batch into statements. A module (view, procedure, ...) runs to the end of the batch."""
    sig = _significant(_tokens(batch))
    out, start, depth, first = [], 0, 0, True
    for i, t in enumerate(sig):
        if not first and depth == 0 and t.kind == WORD and _is_boundary(sig, i):
            out.append(batch[start:t.offset])
            start, first = t.offset, True
        if first:
            first = False
            if _is_module_start(sig, i):
                out.append(batch[start:])
                return [s for s in out if s.strip()]
        if t.kind == SYMBOL:
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth = max(0, depth - 1)
            elif t.text == ";" and depth == 0:
                out.append(batch[start:t.offset + 1])
                start, first = t.offset + 1, True
    out.append(batch[start:])
    return [s for s in out if _significant(_tokens(s))]


# --- storage clauses ---------------------------------------------------------

def _matching_paren(toks: list[Tok], i: int) -> int:
    depth = 0
    for j in range(i, len(toks)):
        if toks[j].kind == SYMBOL and toks[j].text == "(":
            depth += 1
        elif toks[j].kind == SYMBOL and toks[j].text == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(toks) - 1


def _next_sig(toks: list[Tok], i: int) -> int:
    while i < len(toks) and toks[i].kind in TRIVIA:
        i += 1
    return i


def _last_sig(out: list[tuple[str, str]]) -> str:
    for kind, text in reversed(out):
        if kind not in TRIVIA:
            return text
    return ""


def _with_options(toks: list[Tok]) -> str | None:
    """Rebuild a WITH (...) option list without storage options; None if nothing is left."""
    options, current, depth = [], [], 0
    for t in toks[1:-1]:
        if t.kind == SYMBOL and t.text == "(":
            depth += 1
        elif t.kind == SYMBOL and t.text == ")":
            depth -= 1
        if t.kind == SYMBOL and t.text == "," and depth == 0:
            options.append(current)
            current = []
        else:
            current.append(t)
    options.append(current)
    kept = []
    for opt in options:
        sig = _significant(opt)
        if not sig:
            continue
        name = sig[0].up
        value = sig[-1].up
        if name in STORAGE_OPTIONS or (name == "IGNORE_DUP_KEY" and value == "OFF"):
            continue
        kept.append("".join(t.text for t in opt).strip())
    return "(" + ", ".join(kept) + ")" if kept else None


def strip_storage(text: str) -> str:
    """Remove filegroup placement and storage options from table, constraint and index DDL."""
    toks = _tokens(text)
    out: list[tuple[str, str]] = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t.up in ("TEXTIMAGE_ON", "FILESTREAM_ON"):
            j = _next_sig(toks, i + 1)
            if j < n and toks[j].kind in (WORD, QUOTED_ID, STRING):
                _pop_ws(out)
                i = j + 1
                continue
        if t.up == "ON" and _last_sig(out).endswith(")"):
            j = _next_sig(toks, i + 1)
            if j < n and toks[j].kind in (WORD, QUOTED_ID) and toks[j].up not in ("DELETE", "UPDATE"):
                k = _next_sig(toks, j + 1)
                end = _matching_paren(toks, k) if k < n and toks[k].text == "(" else j
                _pop_ws(out)
                i = end + 1
                continue
        if t.up == "WITH":
            j = _next_sig(toks, i + 1)
            if j < n and toks[j].kind == SYMBOL and toks[j].text == "(":
                end = _matching_paren(toks, j)
                rebuilt = _with_options(toks[j:end + 1])
                if rebuilt is None:
                    _pop_ws(out)
                else:
                    out.append((WORD, "WITH"))
                    out.append((WS, " "))
                    out.append((SYMBOL, rebuilt))
                i = end + 1
                continue
        out.append((t.kind, t.text))
        i += 1
    return "".join(text for _, text in out)


def _pop_ws(out: list[tuple[str, str]]) -> None:
    while out and out[-1][0] == WS:
        out.pop()


# --- parsing -----------------------------------------------------------------

@dataclass
class _Pending:
    schema: str
    name: str
    text: str
    file: str


@dataclass
class _State:
    objects: dict[str, tuple[DbObject, str]] = field(default_factory=dict)
    attach: list[_Pending] = field(default_factory=list)
    disable: list[tuple[str, str, str]] = field(default_factory=list)  # (schema, trigger, "DATABASE" or "")
    database: str = ""
    collation: str = ""
    compat_level: int | None = None
    duplicates: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    wrapped: int = 0


def _finish(text: str) -> str:
    """Trim a non-module statement and give it exactly one trailing semicolon."""
    return text.strip().rstrip(";").rstrip() + ";"


class _FileParser:
    def __init__(self, state: _State, file: str):
        self.state, self.file = state, file
        self.ansi_nulls = True
        self.quoted_identifier = True

    def batch(self, batch: str) -> None:
        for stmt in split_statements(batch):
            self.statement(stmt)

    def add(self, obj: DbObject) -> None:
        existing = self.state.objects.get(obj.key)
        if existing is not None:
            self.state.duplicates.append(f"{obj.category} {obj.display_name} ({existing[1]}, {self.file})")
            return
        self.state.objects[obj.key] = (obj, self.file)

    def skip(self, text: str) -> None:
        first_line = text.strip().split("\n", 1)[0].strip()
        self.state.skipped.append(first_line[:80])
        if "sp_executesql" in text.lower():
            self.state.wrapped += 1

    def statement(self, stmt: str) -> None:
        toks = _tokens(stmt)
        sig = _significant(toks)
        if not sig:
            return
        text = stmt[sig[0].offset:]  # drop leading comments, such as the SSMS "Object: ... Script Date" header
        w = [t.up for t in sig[:4]] + [""] * 4
        if _is_module_start(sig, 0):
            self.module(sig, text)
        elif w[0] == "CREATE":
            self.create(sig, text)
        elif w[0] == "ALTER" and w[1] == "TABLE":
            self.alter_table(sig, text)
        elif w[0] == "ALTER" and w[1] == "DATABASE":
            self.alter_database(sig)
        elif w[0] in ("DISABLE", "ENABLE") and w[1] == "TRIGGER":
            if w[0] == "DISABLE":
                parts, i = _read_name(sig, 2)
                target = _read_name(sig, i + 1)[0] if i < len(sig) and sig[i].up == "ON" else []
                self.disable_trigger(parts, target)
        elif w[0] == "SET":
            if w[1] in ("ANSI_NULLS", "QUOTED_IDENTIFIER") and w[2] in ("ON", "OFF"):
                setattr(self, w[1].lower(), w[2] == "ON")
        elif w[0] == "USE":
            parts, _ = _read_name(sig, 1)
            if parts and not self.state.database:
                self.state.database = parts[-1]
        else:
            self.skip(text)

    def module(self, sig: list[Tok], text: str) -> None:
        i = 1
        if sig[0].up == "CREATE" and sig[1].up == "OR":
            i = 3
        category = MODULE_WORDS[sig[i].up]
        parts, j = _read_name(sig, i + 1)
        if sig[0].up == "ALTER":
            text = "CREATE" + text[len("ALTER"):]
        obj = DbObject(category, *_schema_name(parts), definition=text.rstrip(),
                       ansi_nulls=self.ansi_nulls, quoted_identifier=self.quoted_identifier)
        if category == "Trigger":
            if j < len(sig) and sig[j].up == "ON":
                target, _ = _read_name(sig, j + 1)
                upper = [p.upper() for p in target]
                if upper == ["ALL"]:  # ON ALL SERVER: server triggers are not compared
                    self.skip(text)
                    return
                if upper == ["DATABASE"]:
                    obj.schema, obj.parent = "", "DATABASE"
                else:
                    tschema, tname = _schema_name(target)
                    obj.parent = qualified(tschema, tname)
                    if len(parts) < 2:
                        obj.schema = tschema
        self.add(obj)

    def create(self, sig: list[Tok], text: str) -> None:
        w1 = sig[1].up if len(sig) > 1 else ""
        if w1 in SIMPLE_WORDS:
            category = SIMPLE_WORDS[w1]
            parts, _ = _read_name(sig, 2)
            if not parts or parts[-1].startswith("#"):
                self.skip(text)
                return
            if category == "Schema":
                self.add(DbObject("Schema", "", parts[-1], definition=_finish(text)))
                return
            body = strip_storage(text) if category in ("Table", "Type") else text
            self.add(DbObject(category, *_schema_name(parts), definition=_finish(body)))
            return
        if w1 == "DATABASE":
            for k, t in enumerate(sig[:-1]):
                if t.up == "COLLATE":
                    self.state.collation = sig[k + 1].text
            parts, _ = _read_name(sig, 2)
            if parts and not self.state.database:
                self.state.database = parts[-1]
            return
        for k, t in enumerate(sig[:6]):
            if t.up == "INDEX":
                _, j = _read_name(sig, k + 1)
                if j < len(sig) and sig[j].up == "ON":
                    target, _ = _read_name(sig, j + 1)
                    self.state.attach.append(_Pending(*_schema_name(target), _finish(strip_storage(text)), self.file))
                    return
        self.skip(text)

    def alter_table(self, sig: list[Tok], text: str) -> None:
        target, i = _read_name(sig, 2)
        words = [t.up for t in sig[i:i + 3]] + ["", "", ""]
        if words[0] == "WITH" and words[1] == "CHECK" and words[2] == "ADD":
            # WITH CHECK is the default for new constraints.
            text = text[:sig[i].offset - sig[0].offset] + text[sig[i + 2].offset - sig[0].offset:]
            words = ["ADD"]
        if words[0] == "ADD" or (words[0] == "WITH" and words[2] == "ADD") or words[0] == "NOCHECK":
            self.state.attach.append(_Pending(*_schema_name(target), _finish(strip_storage(text)), self.file))
        elif words[0] == "DISABLE" and words[1] == "TRIGGER":
            self.disable_trigger(_read_name(sig, i + 2)[0], target)
        elif words[0] in ("CHECK", "ENABLE", "SET"):
            pass  # re-enabling a constraint or trigger is the default; SET (LOCK_ESCALATION ...) is storage
        else:
            self.skip(text)

    def disable_trigger(self, trigger: list[str], target: list[str]) -> None:
        if not trigger:
            return
        if [p.upper() for p in target] == ["DATABASE"]:
            self.state.disable.append(("", trigger[-1], "DATABASE"))
            return
        schema = trigger[-2] if len(trigger) >= 2 else _schema_name(target)[0] or "dbo"
        self.state.disable.append((schema, trigger[-1], ""))

    def alter_database(self, sig: list[Tok]) -> None:
        for k, t in enumerate(sig[:-2]):
            if t.up == "COMPATIBILITY_LEVEL" and sig[k + 1].text == "=" and sig[k + 2].text.isdigit():
                self.state.compat_level = int(sig[k + 2].text)


def _database_name(names: list[str]) -> str:
    paths = [PurePosixPath(n.replace("\\", "/")) for n in names]
    tops = {p.parts[0] for p in paths if len(p.parts) > 1}
    if len(tops) == 1 and all(len(p.parts) > 1 for p in paths):
        return tops.pop()
    return paths[0].stem if len(paths) == 1 else ""


def parse_scripts(files: list[tuple[str, bytes]], label: str) -> Snapshot:
    """Build a Snapshot from exported DDL script files, given as (relative name, content) pairs."""
    files = sorted(((n, d) for n, d in files if n.lower().endswith(".sql")), key=lambda f: f[0].lower())
    if not files:
        raise ValueError(f"{label}: choose at least one .sql file")
    state = _State()
    for name, data in files:
        parser = _FileParser(state, name)
        for batch in split_batches(decode(data)):
            parser.batch(batch)
    objects = {key: obj for key, (obj, _) in state.objects.items()}
    if not objects:
        raise ValueError(f"{label}: no CREATE statements found in the selected files")

    warnings: list[dict] = []
    orphans = []
    for p in state.attach:
        key_part = f"|{p.schema}|{p.name}".lower()
        owner = objects.get("table" + key_part) or objects.get("view" + key_part)
        if owner is None:
            orphans.append(f"{p.schema}.{p.name}")
        else:
            owner.attached.append(p.text)
    for schema, name, _ in state.disable:
        trig = objects.get(f"trigger|{schema}|{name}".lower())
        if trig is not None:
            trig.disabled = True

    if state.duplicates:
        warnings.append({"level": "warn", "text": (
            f"{len(state.duplicates)} object(s) are defined more than once; the first definition is used: "
            + "; ".join(state.duplicates[:MAX_EXAMPLES]) + ("; …" if len(state.duplicates) > MAX_EXAMPLES else ""))})
    if orphans:
        names = sorted(set(orphans))
        warnings.append({"level": "warn", "text": (
            f"{len(orphans)} constraint/index statement(s) refer to tables that are not in the files and were "
            "ignored: " + ", ".join(names[:MAX_EXAMPLES]) + (", …" if len(names) > MAX_EXAMPLES else ""))})
    if state.wrapped:
        warnings.append({"level": "warn", "text": (
            f"{state.wrapped} statement(s) are wrapped in EXEC sp_executesql and were not read. "
            "In SSMS Generate Scripts, set \"Check for object existence\" to False.")})
    if state.skipped:
        warnings.append({"level": "info", "text": (
            f"{len(state.skipped)} statement(s) that do not define compared objects were skipped, e.g. "
            + "; ".join(state.skipped[:3]))})

    return Snapshot(
        label=label,
        server="Script files",
        database=state.database or _database_name([n for n, _ in files]),
        server_version=f"{len(files)} script file{'s' if len(files) != 1 else ''}",
        collation=state.collation,
        compat_level=state.compat_level,
        extracted_at=datetime.now().isoformat(timespec="seconds"),
        source="scripts",
        warnings=warnings,
        objects=list(objects.values()),
    )
