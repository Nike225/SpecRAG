"""Tokenization tuned for hardware specifications.

Spec text is full of hex literals written several ways (06h, 0x06, 6h), field
mnemonics (CNS, NSID, SQID), bit ranges (31:16) and register names. A generic
tokenizer splits or mangles these; this one canonicalises them so that a query for
"opcode 0x6" matches a table row reading "06h  Identify".
"""
from __future__ import annotations

import re

STOPWORDS = frozenset(
    """a an and are as at be by for from has have how if in into is it its of on or
    that the this to was were what when where which while who why will with does do
    can should shall may must i you we they he she them their our your there than then
    so such any all each other also not no only same about after before between over
    under up down out these those been being would could get""".split()
)

# Expansions applied to *queries only*: users type the acronym, specs often spell it out
# (and vice versa). Kept deliberately to unambiguous NVMe/PCIe acronyms.
ACRONYMS: dict[str, str] = {
    "cns": "controller namespace structure",
    "nsid": "namespace identifier",
    "ns": "namespace",
    "lba": "logical block address",
    "sq": "submission queue",
    "cq": "completion queue",
    "sqid": "submission queue identifier",
    "cqid": "completion queue identifier",
    "cid": "command identifier",
    "fid": "feature identifier",
    "lid": "log page identifier",
    "aer": "asynchronous event request",
    "smart": "health information",
    "fw": "firmware",
    "wp": "write protect",
    "apst": "autonomous power state transition",
    "hmb": "host memory buffer",
    "sgl": "scatter gather list",
    "prp": "physical region page",
    "dptr": "data pointer",
    "ctrl": "controller",
    "nvm": "non-volatile memory",
}

_HEX_0X = re.compile(r"\b0x([0-9a-f]{1,8})\b")
_HEX_H = re.compile(r"\b([0-9a-f]{1,8})h\b")
_TOKEN = re.compile(r"\d+:\d+|[a-z0-9]+(?:[_\-][a-z0-9]+)*")


def _canon_hex(digits: str) -> str:
    return f"{int(digits, 16):02x}h"


def normalize(text: str) -> str:
    t = text.lower()
    t = _HEX_0X.sub(lambda m: _canon_hex(m.group(1)), t)
    # only treat "NNh" as hex when it looks like a spec literal (has a digit)
    t = _HEX_H.sub(lambda m: _canon_hex(m.group(1)) if re.search(r"\d", m.group(1)) else m.group(0), t)
    return t


def _stem(tok: str) -> str:
    if len(tok) <= 4 or tok[0].isdigit():
        return tok
    for suf in ("ations", "ation", "ing", "ies", "ers", "ed", "es", "er", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)] + ("y" if suf == "ies" else "")
    return tok


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for tok in _TOKEN.findall(normalize(text)):
        if tok in STOPWORDS:
            continue
        out.append(_stem(tok))
        if "-" in tok or "_" in tok:  # also index the parts of compound identifiers
            out.extend(_stem(p) for p in re.split(r"[-_]", tok) if p and p not in STOPWORDS)
    return out


def expand_query(query: str) -> str:
    extra = [ACRONYMS[t] for t in re.findall(r"[a-z]+", query.lower()) if t in ACRONYMS]
    return query if not extra else f"{query} {' '.join(extra)}"
