"""Exact matching of a text locator against OCR detections, the same for every OCR engine."""
import unicodedata
from collections.abc import Iterable, Sequence
from itertools import accumulate
from operator import itemgetter
from typing import NamedTuple

# Scripts written without spaces between words: each character is a token there.
_UNSPACED = (
    (0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1780, 0x17FF), (0x3040, 0x30FF), (0x31F0, 0x31FF),
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF66, 0xFF9F), (0x20000, 0x3FFFF),
)

Point = tuple[int, int]


class TextMatch(NamedTuple):
    center: Point
    box: tuple[Point, Point]


class _Token(NamedTuple):
    text: str
    left: float
    top: float
    right: float
    bottom: float
    detection: int
    order: int


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _in_word(char: str) -> bool:
    return char == "_" or unicodedata.category(char)[0] in "LNM"


def _unspaced(char: str) -> bool:
    return any(low <= ord(char) <= high for low, high in _UNSPACED)


def _joins_word(char: str) -> bool:
    return _in_word(char) and not _unspaced(char)


def _word_goes_on(text: str, i: int) -> bool:
    """A letter, or an apostrophe between letters as in ``can't``."""
    return _joins_word(text[i]) or (text[i] in "'’" and i + 1 < len(text) and _joins_word(text[i + 1]))


def _bidi_class(char: str) -> str:
    """L or R for a letter, N for a number, empty for a neutral character."""
    kind = unicodedata.bidirectional(char)
    if kind in ("R", "AL"):
        return "R"
    if kind in ("EN", "AN"):
        return "N"
    return "L" if kind == "L" else ""


def _direction(text: str) -> int:
    """1 for left to right, -1 for right to left, 0 when the text has no letter that sets a direction."""
    strong = (unicodedata.bidirectional(char) for char in text)
    return next(({"L": 1, "R": -1, "AL": -1}[kind] for kind in strong if kind in ("L", "R", "AL")), 0)


def _levels(text: str) -> list[int]:
    """Embedding levels by the Unicode bidirectional rules W7, N1 and I1-I2, for text with no explicit embeddings."""
    base = "R" if _direction(text) < 0 else "L"
    kinds = [_bidi_class(char) for char in text]
    last = base
    for i, kind in enumerate(kinds):
        if kind in ("L", "R"):
            last = kind
        elif kind == "N" and last == "L":
            kinds[i] = "L"
    strong = ["R" if kind == "N" else kind for kind in kinds]
    for i, kind in enumerate(kinds):
        if not kind:
            before = next((k for k in reversed(strong[:i]) if k), base)
            after = next((k for k in strong[i + 1:] if k), base)
            kinds[i] = before if before == after else base
    level = {"L": {"L": 0, "R": 1, "N": 2}, "R": {"L": 2, "R": 1, "N": 2}}[base]
    return [level[kind] for kind in kinds]


def _reverse_runs(order: list[int], levels: list[int], level: int) -> None:
    i = 0
    while i < len(order):
        end = i
        while end < len(order) and levels[order[end]] >= level:
            end += 1
        order[i:end] = order[i:end][::-1]
        i = end + 1


def _slots(text: str) -> list[int]:
    """Each character's position from the left as drawn: rule L2 reverses every run at each level down to 1."""
    levels = _levels(text)
    order = list(range(len(text)))
    for level in range(max(levels, default=0), 0, -1):
        _reverse_runs(order, levels, level)
    slots = [0] * len(text)
    for position, i in enumerate(order):
        slots[i] = position
    return slots


def _spans(text: str) -> Iterable[tuple[int, int]]:
    """A token is a word, or one character of an unspaced script or punctuation, with its combining marks."""
    end = 0
    while end < len(text):
        start, end = end, end + 1
        if text[start].isspace():
            continue
        word = _joins_word(text[start])
        while end < len(text) and (unicodedata.category(text[end])[0] == "M" or (word and _word_goes_on(text, end))):
            end += 1
        yield start, end


def _tokens(bbox: Sequence[Sequence[float]], text: str, detection: int) -> list[_Token]:
    """Split one detection into tokens in reading order, sharing its box out by where each character is drawn."""
    text = _normalized(text)
    if not text or not bbox:
        return []
    xs, ys = [p[0] for p in bbox], [p[1] for p in bbox]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    step = (right - left) / len(text)
    slots = _slots(text)
    tokens = []
    for order, (start, end) in enumerate(_spans(text)):
        drawn = [slots[i] for i in range(start, end)]
        tokens.append(_Token(text[start:end].casefold(), left + min(drawn) * step, top, left + (max(drawn) + 1) * step, bottom, detection, order))
    return tokens


def _same_row(a: _Token, b: _Token) -> bool:
    """The shorter box's middle is inside the taller box's height, so a tight comma box stays on its line."""
    short, tall = sorted((a, b), key=lambda t: t.bottom - t.top)
    return tall.top <= (short.top + short.bottom) / 2 <= tall.bottom


def _rows(tokens: Iterable[_Token]) -> list[list[_Token]]:
    rows: list[list[_Token]] = []
    for token in sorted(tokens, key=lambda t: t.top + t.bottom):
        row = rows[-1] if rows else None
        if row and _same_row(token, row[0]):
            row.append(token)
        else:
            rows.append([token])
    return rows


def _runs(row: list[_Token]) -> list[list[_Token]]:
    """Cut a row between detections further apart than the text is tall: those belong to separate controls."""
    first: dict[int, float] = {}
    for token in row:
        first[token.detection] = min(first.get(token.detection, token.left), token.left)
    runs: list[list[_Token]] = []
    for token in sorted(row, key=lambda t: (first[t.detection], t.detection, t.left)):
        last = runs[-1][-1] if runs else None
        near = last and token.left - last.right <= max(token.bottom - token.top, last.bottom - last.top)
        if last and (near or token.detection == last.detection):
            runs[-1].append(token)
        else:
            runs.append([token])
    return [_reading_order(run) for run in runs]


def _reading_order(run: list[_Token]) -> list[_Token]:
    """Detections in the run's direction, each keeping its own words in the order they were read."""
    detections: dict[int, list[_Token]] = {}
    for token in run:
        detections.setdefault(token.detection, []).append(token)
    for words in detections.values():
        words.sort(key=lambda t: t.order)
    ordered = sorted(detections.values(), key=lambda words: min(t.left for t in words), reverse=sum(_direction(t.text) for t in run) < 0)
    return [token for words in ordered for token in words]


def _extent(run: list[_Token]) -> tuple[float, float, float, float]:
    return min(t.left for t in run), min(t.top for t in run), max(t.right for t in run), max(t.bottom for t in run)


def _overlap(above: list[_Token], below: list[_Token]) -> float:
    (a_left, _, a_right, _), (b_left, _, b_right, _) = _extent(above), _extent(below)
    return min(a_right, b_right) - max(a_left, b_left)


def _continues(above: list[_Token], below: list[_Token]) -> bool:
    """A run continues on the next line when the two overlap sideways and the line gap is no taller than the text."""
    (_, a_top, _, a_bottom), (_, b_top, _, b_bottom) = _extent(above), _extent(below)
    return _overlap(above, below) > 0 and b_top - a_bottom <= min(a_bottom - a_top, b_bottom - b_top)


def _within_reach(above: list[_Token], top: float) -> bool:
    _, a_top, _, a_bottom = _extent(above)
    return top - a_bottom <= a_bottom - a_top


def _blocks(rows: list[list[_Token]]) -> list[list[_Token]]:
    """Join each line's runs with the runs they wrap onto, so wrapped text reads as one sequence.

    A line stays open past rows in other columns, until no later row starts near enough below it.
    """
    blocks: list[list[_Token]] = []
    open_runs: list[tuple[list[_Token], list[_Token]]] = []
    later_tops = list(accumulate(reversed([min(t.top for t in row) for row in rows]), min))[::-1]
    for row, later_top in zip(rows, later_tops):
        open_runs = [(above, b) for above, b in open_runs if _within_reach(above, later_top)]
        added = []
        for run in _runs(row):
            eligible = [(_overlap(a, run), (a, b)) for a, b in reversed(open_runs) if _continues(a, run)]
            above = max(eligible, key=itemgetter(0), default=(0, None))[1]
            if above is None:
                block = []
                blocks.append(block)
            else:
                block = above[1]
                open_runs = [(a, b) for a, b in open_runs if b is not block]
            block.extend(run)
            added.append((run, block))
        open_runs += added
    return blocks


def _matches_in(tokens: list[_Token], wanted: str, strict: bool) -> Iterable[tuple[bool, _Token, TextMatch]]:
    """Each place ``wanted`` appears, with whether it is whole words; a part of a word counts only when not ``strict``."""
    joined = "".join(t.text for t in tokens)
    starts, offset = {}, 0
    for i, token in enumerate(tokens):
        starts[offset] = i
        offset += len(token.text)
    ends = {o + len(tokens[i].text): i for o, i in starts.items()}
    seen = set()
    at = joined.find(wanted)
    while at != -1:
        end = at + len(wanted)
        whole = at in starts and end in ends
        words = [tokens[i] for o, i in starts.items() if o < end and o + len(tokens[i].text) > at]
        if (whole or not strict) and (id(words[0]), id(words[-1])) not in seen:
            seen.add((id(words[0]), id(words[-1])))
            left, top = min(w.left for w in words), min(w.top for w in words)
            right, bottom = max(w.right for w in words), max(w.bottom for w in words)
            box = ((round(left), round(top)), (round(right), round(bottom)))
            # A wrapped match's middle falls between its lines, so the tap goes to the middle of its first line.
            line = [w for w in words if _same_row(w, words[0])]
            x = (min(w.left for w in line) + max(w.right for w in line)) / 2
            y = (min(w.top for w in line) + max(w.bottom for w in line)) / 2
            yield whole, words[0], TextMatch((round(x), round(y)), box)
        at = joined.find(wanted, at + 1)


def find_text(detections: Iterable[tuple], text: str, strict: bool = False) -> list[TextMatch]:
    """Every place ``text`` appears, on one line or wrapped onto the next: whole words first, each group in reading order.

    Case and the spaces between words are ignored, since OCR often gets both wrong. When ``strict``,
    only whole words count, so ``Pay`` does not find ``Payment``; otherwise a part of a word counts too.
    """
    wanted = "".join(_normalized(text).split()).casefold()
    if not wanted:
        return []
    tokens = [token for number, (bbox, detected, *_) in enumerate(detections) for token in _tokens(bbox, detected, number)]
    rows = _rows(tokens)
    row_of = {id(token): number for number, row in enumerate(rows) for token in row}
    found = [found for block in _blocks(rows) for found in _matches_in(block, wanted, strict)]
    return [match for _, _, match in sorted(found, key=lambda f: (not f[0], row_of[id(f[1])], f[1].left))]
