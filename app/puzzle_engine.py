"""Server-side crossword puzzle generator & word validator for Solo and Duo matches."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import time
from typing import Dict, List, Optional, Set, Tuple

_DATA_DIR = Path(__file__).resolve().parent / "data"
_FALLBACK_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "data"


def _letter_counts(w: str) -> List[int]:
    counts = [0] * 26
    for ch in w:
        idx = ord(ch) - 97
        if 0 <= idx < 26:
            counts[idx] += 1
    return counts


def _can_form(word_counts: List[int], pool_counts: List[int]) -> bool:
    for i in range(26):
        if word_counts[i] > pool_counts[i]:
            return False
    return True


@dataclass
class _Placement:
    word: str
    row: int
    col: int
    horizontal: bool

    def cell(self, i: int) -> Tuple[int, int]:
        return (self.row, self.col + i) if self.horizontal else (self.row + i, self.col)


class ServerPuzzleEngine:
    MAX_ROWS = 10
    MAX_COLS = 9

    def __init__(self) -> None:
        self.common: List[str] = []
        self.common_counts: List[List[int]] = []
        self.all_words: Set[str] = set()
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        common_path = _DATA_DIR / "common.txt"
        dict_path = _DATA_DIR / "dictionary.txt"
        if not common_path.exists():
            common_path = _FALLBACK_ASSETS / "common.txt"
        if not dict_path.exists():
            dict_path = _FALLBACK_ASSETS / "dictionary.txt"

        if common_path.exists():
            for line in common_path.read_text(encoding="utf-8").splitlines():
                w = line.strip().lower()
                if w:
                    self.common.append(w)
                    self.common_counts.append(_letter_counts(w))
                    self.all_words.add(w)
        if dict_path.exists():
            for line in dict_path.read_text(encoding="utf-8").splitlines():
                w = line.strip().lower()
                if w:
                    self.all_words.add(w)
        self._loaded = True

    def is_valid_word(self, word: str) -> bool:
        self.load()
        return word.strip().lower() in self.all_words

    def generate(self, level: int = 5) -> Dict:
        self.load()
        rnd = random.Random()
        base_len = 4 if level <= 3 else (5 if level <= 10 else (6 if level <= 25 else 7))
        max_words = min(4 + level // 4, 5 if base_len == 4 else 9)

        for length in range(base_len, 3, -1):
            min_words = 3 if length == 4 else 4
            bases = [i for i, w in enumerate(self.common) if len(w) == length]
            rnd.shuffle(bases)

            for bi in bases[:120]:
                base = self.common[bi]
                pool = self.common_counts[bi]
                subs = [
                    self.common[i]
                    for i in range(len(self.common))
                    if i != bi
                    and len(self.common[i]) <= length
                    and _can_form(self.common_counts[i], pool)
                ]
                if len(subs) + 1 < min_words:
                    continue

                subs.sort(key=lambda s: len(s) + rnd.random() * 1.6, reverse=True)
                candidates = [base, *subs[: max_words * 3]]
                layout = self._best_layout(candidates, max_words, rnd)
                if layout and len(layout) >= min_words:
                    return self._build(base, layout, rnd)

        # Fallback guaranteed puzzle
        return {
            "id": f"{int(time.time() * 1000000)}_planet",
            "letters": ["p", "l", "a", "n", "e", "t"],
            "words": [
                {"w": "planet", "r": 0, "c": 0, "h": True},
                {"w": "plan", "r": 0, "c": 0, "h": False},
                {"w": "late", "r": 1, "c": 0, "h": True},
                {"w": "neat", "r": 3, "c": 0, "h": True},
                {"w": "tale", "r": 0, "c": 5, "h": False},
            ],
            "rows": 4,
            "cols": 6,
        }

    def _best_layout(
        self, words: List[str], max_words: int, rnd: random.Random
    ) -> Optional[List[_Placement]]:
        best: Optional[List[_Placement]] = None
        best_score = float("-inf")
        for _ in range(24):
            rest = words[1:]
            rest.sort(key=lambda w: len(w) + rnd.random() * 2.5, reverse=True)
            placed = self._try_layout([words[0], *rest], max_words, rnd)
            score = self._score(placed)
            if score > best_score:
                best_score = score
                best = placed
        return best

    def _try_layout(
        self, order: List[str], max_words: int, rnd: random.Random
    ) -> List[_Placement]:
        grid: Dict[Tuple[int, int], str] = {}
        dirs: Dict[Tuple[int, int], int] = {}
        placed: List[_Placement] = []

        def put(p: _Placement) -> None:
            for i, ch in enumerate(p.word):
                c = p.cell(i)
                grid[c] = ch
                dirs[c] = dirs.get(c, 0) | (1 if p.horizontal else 2)
            placed.append(p)

        put(_Placement(order[0], 0, 0, rnd.choice([True, False])))

        for w in order[1:]:
            if len(placed) >= max_words:
                break
            options: List[Tuple[_Placement, int]] = []
            for pw in placed:
                for i, pch in enumerate(pw.word):
                    for j, wch in enumerate(w):
                        if wch != pch:
                            continue
                        p = (
                            _Placement(w, pw.row - j, pw.col + i, False)
                            if pw.horizontal
                            else _Placement(w, pw.row + i, pw.col - j, True)
                        )
                        inter = self._fits(p, grid, dirs)
                        if inter > 0 and self._within_bounds(p, placed):
                            options.append((p, inter))
            if not options:
                continue
            rnd.shuffle(options)
            options.sort(key=lambda item: item[1], reverse=True)
            put(options[0][0])
        return placed

    def _fits(
        self,
        p: _Placement,
        grid: Dict[Tuple[int, int], str],
        dirs: Dict[Tuple[int, int], int],
    ) -> int:
        bit = 1 if p.horizontal else 2
        before = (p.row, p.col - 1) if p.horizontal else (p.row - 1, p.col)
        after = (
            (p.row, p.col + len(p.word))
            if p.horizontal
            else (p.row + len(p.word), p.col)
        )
        if before in grid or after in grid:
            return 0

        inter = 0
        for i, ch in enumerate(p.word):
            c = p.cell(i)
            existing = grid.get(c)
            if existing is not None:
                if existing != ch or (dirs[c] & bit) != 0:
                    return 0
                inter += 1
            else:
                n1 = (c[0] - 1, c[1]) if p.horizontal else (c[0], c[1] - 1)
                n2 = (c[0] + 1, c[1]) if p.horizontal else (c[0], c[1] + 1)
                if n1 in grid or n2 in grid:
                    return 0
        return inter

    def _bounds(self, ps: List[_Placement]) -> Tuple[int, int, int, int]:
        min_r = min(p.row for p in ps)
        min_c = min(p.col for p in ps)
        max_r = max(p.cell(len(p.word) - 1)[0] for p in ps)
        max_c = max(p.cell(len(p.word) - 1)[1] for p in ps)
        return min_r, min_c, max_r, max_c

    def _within_bounds(self, p: _Placement, placed: List[_Placement]) -> bool:
        min_r, min_c, max_r, max_c = self._bounds([*placed, p])
        return (
            max_r - min_r + 1 <= self.MAX_ROWS
            and max_c - min_c + 1 <= self.MAX_COLS
        )

    def _score(self, placed: List[_Placement]) -> float:
        min_r, min_c, max_r, max_c = self._bounds(placed)
        rows = max_r - min_r + 1
        cols = max_c - min_c + 1
        letters = sum(len(p.word) for p in placed)
        return len(placed) * 100 + letters * 3 - rows * cols * 0.9 - abs(rows - cols) * 2

    def _build(
        self, base: str, placed: List[_Placement], rnd: random.Random
    ) -> Dict:
        min_r, min_c, max_r, max_c = self._bounds(placed)
        letters = list(base)
        while len(letters) > 1 and "".join(letters) == base:
            rnd.shuffle(letters)
        return {
            "id": f"{int(time.time() * 1000000)}_{base}",
            "letters": letters,
            "words": [
                {
                    "w": p.word,
                    "r": p.row - min_r,
                    "c": p.col - min_c,
                    "h": p.horizontal,
                }
                for p in placed
            ],
            "rows": max_r - min_r + 1,
            "cols": max_c - min_c + 1,
        }


puzzle_engine = ServerPuzzleEngine()
