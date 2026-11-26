"""De Bruijn cyclic patterns, compatible in spirit with pwntools' cyclic()."""

from __future__ import annotations

import string

_ALPHABET = string.ascii_lowercase
_N = 4
_cache: bytes | None = None


def _de_bruijn(alphabet: str, n: int) -> str:
    k = len(alphabet)
    a = [0] * k * n
    seq: list[int] = []

    def db(t: int, p: int) -> None:
        if t > n:
            if n % p == 0:
                seq.extend(a[1 : p + 1])
        else:
            a[t] = a[t - p]
            db(t + 1, p)
            for j in range(a[t - p] + 1, k):
                a[t] = j
                db(t + 1, t)

    db(1, 1)
    return "".join(alphabet[i] for i in seq)


def _pattern() -> bytes:
    global _cache
    if _cache is None:
        _cache = _de_bruijn(_ALPHABET, _N).encode()
    return _cache


def cyclic(length: int) -> bytes:
    pat = _pattern()
    if length > len(pat):
        raise ValueError(f"pattern exhausted: max length is {len(pat)}")
    return pat[:length]


def cyclic_find(value: int | bytes | str) -> int:
    """Offset of ``value`` inside the pattern, or -1.

    Integers are interpreted as little-endian register contents; only the
    first four bytes matter (as with pwntools' default n=4 subsequences).
    """
    if isinstance(value, int):
        width = 4 if value < 2**32 else 8
        raw = value.to_bytes(width, "little")
    elif isinstance(value, str):
        raw = value.encode()
    else:
        raw = bytes(value)
    return _pattern().find(raw[:_N])
