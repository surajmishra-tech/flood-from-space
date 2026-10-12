"""Place names: show Nepali (Devanagari) names in Latin letters for English reports.

Many villages in OpenStreetMap only have a Nepali name (e.g. रसुवागढी).
This simple rule-based transliteration gives a readable English spelling
("Rasuvagadhi"). It is approximate - good enough to recognise a place.
"""
from __future__ import annotations

import re

CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
    "ञ": "ny", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d",
    "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r",
    "ल": "l", "व": "w", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "क्ष": "ksh", "ज्ञ": "gy",
}
VOWELS = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ओ": "o", "औ": "au"}
MATRAS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au"}
VIRAMA, NUKTA = "्", "़"
NASAL = {"ं": "n", "ँ": "n", "ः": "h"}
DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
TO_NEPALI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def is_devanagari(text: str) -> bool:
    return bool(DEVANAGARI.search(text or ""))


def romanize(text: str) -> str:
    """Devanagari -> simple Latin spelling. Other text is returned unchanged."""
    if not text or not is_devanagari(text):
        return text or ""
    words = []
    for word in text.split():
        out, i = [], 0
        while i < len(word):
            ch = word[i]
            nxt = word[i + 1] if i + 1 < len(word) else ""
            if ch in CONSONANTS:
                if nxt == NUKTA:
                    i += 1
                    nxt = word[i + 1] if i + 1 < len(word) else ""
                out.append(CONSONANTS[ch])
                if nxt == VIRAMA:
                    i += 2
                    continue
                if nxt in MATRAS:
                    out.append(MATRAS[nxt])
                    i += 2
                    continue
                out.append("a")  # inherent vowel
            elif ch in VOWELS:
                out.append(VOWELS[ch])
            elif ch in NASAL:
                out.append(NASAL[ch])
            elif ch in MATRAS:
                out.append(MATRAS[ch])
            else:
                out.append(ch.translate(DIGITS))
            i += 1
        w = "".join(out)
        if len(w) > 3 and w.endswith("a") and not w.endswith("aa") and word[-1] in CONSONANTS:
            w = w[:-1]  # Nepali usually drops the final inherent "a"
        words.append(w)
    return " ".join(w[:1].upper() + w[1:] for w in words)


def english_name(row) -> str:
    """Best English label for an OSM feature (name:en, else romanised name)."""
    for k in ("name:en", "name"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return romanize(v.strip())
    return ""


def nepali_name(row) -> str:
    for k in ("name:ne", "name"):
        v = row.get(k)
        if isinstance(v, str) and v.strip() and is_devanagari(v):
            return v.strip()
    return ""  # no Nepali spelling: reports fall back to the English name


def to_nepali_digits(text: str) -> str:
    return text.translate(TO_NEPALI_DIGITS)
