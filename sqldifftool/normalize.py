"""T-SQL aware text normalization.

A small lexer splits text into whitespace, comments, string literals, quoted
identifiers, words and symbols, so that options like "ignore case" never touch
the contents of string literals, and "--" inside a string is not a comment.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

WS, LINE_COMMENT, BLOCK_COMMENT, STRING, QUOTED_ID, WORD, SYMBOL = (
    "ws", "lcom", "bcom", "str", "qid", "word", "sym")


@dataclass(frozen=True)
class CompareOptions:
    ignore_whitespace: bool = True
    ignore_system_names: bool = True
    ignore_case: bool = False
    ignore_comments: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> CompareOptions:
        data = data or {}
        return cls(**{f.name: bool(data[f.name]) for f in fields(cls) if f.name in data})

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c in "_@#$"


def _quoted_end(text: str, start: int, close: str) -> int:
    """Index just past a literal that opened at `start`; a doubled `close` is an escape."""
    n = len(text)
    j = start + 1
    while True:
        k = text.find(close, j)
        if k == -1:
            return n
        if k + 1 < n and text[k + 1] == close:
            j = k + 2
            continue
        return k + 1


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            kind = WS
        elif text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            kind = LINE_COMMENT
        elif text.startswith("/*", i):
            # T-SQL block comments nest.
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            kind = BLOCK_COMMENT
        elif c == "'":
            j, kind = _quoted_end(text, i, "'"), STRING
        elif c == "[":
            j, kind = _quoted_end(text, i, "]"), QUOTED_ID
        elif c == '"':
            j, kind = _quoted_end(text, i, '"'), QUOTED_ID
        elif _is_word_char(c):
            j = i + 1
            while j < n and _is_word_char(text[j]):
                j += 1
            kind = WORD
        else:
            j, kind = i + 1, SYMBOL
        tokens.append((kind, text[i:j]))
        i = j
    return tokens


def normalize_base(text: str) -> str:
    """Normalization that is always applied, and is what gets displayed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return _strip_or_alter("\n".join(lines))


def _strip_or_alter(text: str) -> str:
    """Rewrite a leading `CREATE OR ALTER` to `CREATE`."""
    tokens = tokenize(text)
    words: list[tuple[int, int]] = []  # (token index, offset)
    offset = 0
    for idx, (kind, tok) in enumerate(tokens):
        if kind == WORD:
            words.append((idx, offset))
            if len(words) == 3:
                break
        elif kind not in (WS, LINE_COMMENT, BLOCK_COMMENT):
            return text
        offset += len(tok)
    if len(words) < 3:
        return text
    if [tokens[idx][1].upper() for idx, _ in words] != ["CREATE", "OR", "ALTER"]:
        return text
    create_end = words[0][1] + len("CREATE")
    alter_end = words[2][1] + len(tokens[words[2][0]][1])
    return text[:create_end] + text[alter_end:]


def _transform(tokens: list[tuple[str, str]], opts: CompareOptions) -> list[tuple[str, str]]:
    out = []
    for kind, tok in tokens:
        if kind in (LINE_COMMENT, BLOCK_COMMENT) and opts.ignore_comments:
            # Keep the newline count so line-based keys stay aligned with the text.
            out.append((WS, "\n" * tok.count("\n") or " "))
            continue
        if opts.ignore_case and kind != STRING:
            tok = tok.lower()
        out.append((kind, tok))
    return out


def _join_ignoring_ws(parts: list[tuple[str, str]]) -> str:
    """Join tokens, dropping whitespace unless it separates two word characters."""
    out: list[str] = []
    pending_space = False
    for kind, tok in parts:
        if kind == WS:
            pending_space = True
            continue
        if pending_space and out and _is_word_char(out[-1][-1]) and _is_word_char(tok[0]):
            out.append(" ")
        pending_space = False
        out.append(tok)
    return "".join(out)


def comparison_key(text: str, opts: CompareOptions) -> str:
    """The string two definitions are compared on to decide identical/different."""
    toks = _transform(tokenize(text), opts)
    if opts.ignore_whitespace:
        return _join_ignoring_ws(toks)
    s = "".join(tok for _, tok in toks)
    if opts.ignore_comments:
        # Removed comments leave blank or trailing-space lines behind.
        s = "\n".join(line.rstrip() for line in s.split("\n") if line.strip())
    return s


def line_keys(text: str, opts: CompareOptions) -> list[str]:
    """One comparison key per line of `text` (same length as text.split('\\n'))."""
    toks = _transform(tokenize(text), opts)
    lines: list[list[tuple[str, str]]] = [[]]
    for kind, tok in toks:
        for idx, piece in enumerate(tok.split("\n")):
            if idx:
                lines.append([])
            if piece:
                lines[-1].append((kind, piece))
    if opts.ignore_whitespace:
        return [_join_ignoring_ws(parts) for parts in lines]
    return ["".join(tok for _, tok in parts).rstrip() for parts in lines]
