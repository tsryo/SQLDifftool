"""Line-level side-by-side diff rows with intra-line change segments.

Row shape (compact, sent to the browser):
    t   "e" equal | "c" changed on both sides | "l" left-only line | "r" right-only line
    l/r 1-based line numbers (absent when the side has no line)
    lt/rt line text
    ls/rs intra-line segments [[text, changed(0/1)], ...] for "c" rows when lines are similar enough
"""
from __future__ import annotations

import difflib
import re

from .normalize import CompareOptions, line_keys

_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")
MIN_SIMILARITY = 0.3  # below this, highlighting pieces of a line is more noise than help
PAIR_SIMILARITY = 0.5  # lines in a changed block are shown side by side only if this similar
MAX_ALIGN_CELLS = 40_000  # beyond this block size, fall back to pairing lines by position


def _token_key(tok: str, opts: CompareOptions) -> str:
    if opts.ignore_whitespace and tok.isspace():
        return " "
    return tok.lower() if opts.ignore_case else tok


def inline_segments(a: str, b: str, opts: CompareOptions) -> tuple[list, list] | None:
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    sm = difflib.SequenceMatcher(None, [_token_key(t, opts) for t in ta],
                                 [_token_key(t, opts) for t in tb], autojunk=False)
    if sm.ratio() < MIN_SIMILARITY:
        return None
    left: list[list] = []
    right: list[list] = []

    def add(segs: list[list], text: str, changed: int) -> None:
        if not text:
            return
        if segs and segs[-1][1] == changed:
            segs[-1][0] += text
        else:
            segs.append([text, changed])

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        changed = 0 if tag == "equal" else 1
        add(left, "".join(ta[i1:i2]), changed)
        add(right, "".join(tb[j1:j2]), changed)
    return left, right


def _align_block(ka: list[str], kb: list[str]) -> list[tuple[int | None, int | None]]:
    """Order-preserving alignment of a changed block that pairs up similar lines.

    Returns (left index, right index) pairs; None marks a line that exists on one side only.
    """
    m, n = len(ka), len(kb)
    if m == 0 or n == 0 or m * n > MAX_ALIGN_CELLS:
        return [(i if i < m else None, i if i < n else None) for i in range(max(m, n))]
    sim = [[0.0] * n for _ in range(m)]
    for i in range(m):
        sm = difflib.SequenceMatcher(None, autojunk=False)
        sm.set_seq2(ka[i])
        for j in range(n):
            sm.set_seq1(kb[j])
            if sm.real_quick_ratio() >= PAIR_SIMILARITY and sm.quick_ratio() >= PAIR_SIMILARITY:
                r = sm.ratio()
                sim[i][j] = r if r >= PAIR_SIMILARITY else 0.0
    # score[i][j]: best total similarity aligning ka[:i] with kb[:j]
    score = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            best = max(score[i - 1][j], score[i][j - 1])
            if sim[i - 1][j - 1]:
                best = max(best, score[i - 1][j - 1] + sim[i - 1][j - 1])
            score[i][j] = best
    pairs: list[tuple[int | None, int | None]] = []
    i, j = m, n
    while i > 0 and j > 0:
        if sim[i - 1][j - 1] and score[i][j] == score[i - 1][j - 1] + sim[i - 1][j - 1]:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j]:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs += [(k, None) for k in range(i - 1, -1, -1)]
    pairs += [(None, k) for k in range(j - 1, -1, -1)]
    pairs.reverse()
    # Keep unmatched runs between pairs side by side (like a positional diff) instead of stair-stepping.
    merged: list[tuple[int | None, int | None]] = []
    lefts: list[int] = []
    rights: list[int] = []

    def flush() -> None:
        for k in range(max(len(lefts), len(rights))):
            merged.append((lefts[k] if k < len(lefts) else None, rights[k] if k < len(rights) else None))
        lefts.clear()
        rights.clear()

    for li, rj in pairs:
        if li is not None and rj is not None:
            flush()
            merged.append((li, rj))
        elif li is not None:
            lefts.append(li)
        else:
            rights.append(rj)
    flush()
    return merged


def diff_rows(left_text: str | None, right_text: str | None, opts: CompareOptions) -> list[dict]:
    a = left_text.split("\n") if left_text else []
    b = right_text.split("\n") if right_text else []
    ka = line_keys(left_text, opts) if left_text else []
    kb = line_keys(right_text, opts) if right_text else []
    rows: list[dict] = []
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"t": "e", "l": i1 + k + 1, "r": j1 + k + 1, "lt": a[i1 + k], "rt": b[j1 + k]})
            continue
        for bi, bj in _align_block(ka[i1:i2], kb[j1:j2]):
            li = None if bi is None else i1 + bi
            rj = None if bj is None else j1 + bj
            if li is not None and rj is not None:
                row = {"t": "c", "l": li + 1, "r": rj + 1, "lt": a[li], "rt": b[rj]}
                segs = inline_segments(a[li], b[rj], opts)
                if segs:
                    row["ls"], row["rs"] = segs
            elif li is not None:
                row = {"t": "l", "l": li + 1, "lt": a[li]}
            else:
                row = {"t": "r", "r": rj + 1, "rt": b[rj]}
            rows.append(row)
    return rows


def change_counts(left_text: str, right_text: str, opts: CompareOptions) -> tuple[int, int]:
    """Number of changed lines on the left side and on the right side."""
    sm = difflib.SequenceMatcher(None, line_keys(left_text, opts), line_keys(right_text, opts), autojunk=False)
    left = right = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            left += i2 - i1
            right += j2 - j1
    return left, right
