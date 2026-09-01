"""Deterministic slugs for catalogue and vocabulary fixtures.

Three alphabets, one transliteration table:

* :func:`slugify` — category slugs, option codes, vocabulary term codes: ``[a-z0-9-]``.
* :func:`feature_slug` — feature slugs: ``[a-z0-9_]``, camelCase tags split into words.
* :func:`vocabulary_slug` — a source file's basename plus an optional namespace
  prefix.

The table is fixed on purpose: the same label must produce the same code on
every run, on every machine, forever — fixtures are reviewed as code, and a
term code is the identity a facet, a rule and a listing's stored value all
address.
"""

from __future__ import annotations

import hashlib
import re

CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Ukrainian/Belarusian letters that show up in brand names.
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
}

# Latin-1 letters that appear in brand names ("Citroën", "Škoda", "Björn").
LATIN_FOLD = {
    "á": "a", "à": "a", "â": "a", "ä": "a", "ã": "a", "å": "a", "æ": "ae",
    "ç": "c", "č": "c", "ć": "c", "é": "e", "è": "e", "ê": "e", "ë": "e",
    "ě": "e", "í": "i", "ì": "i", "î": "i", "ï": "i", "ñ": "n", "ń": "n",
    "ó": "o", "ò": "o", "ô": "o", "ö": "o", "õ": "o", "ø": "o", "ř": "r",
    "š": "s", "ś": "s", "ß": "ss", "ú": "u", "ù": "u", "û": "u", "ü": "u",
    "ý": "y", "ÿ": "y", "ž": "z", "ź": "z", "ż": "z", "ł": "l", "đ": "d",
    "þ": "th", "ð": "d",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_CAMEL_1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_NON_FEATURE = re.compile(r"[^a-z0-9]+")

#: The fixture schema caps ``slug`` at 64 characters. The default stem budget
#: below leaves room for a short namespace prefix and its separator; a caller
#: that wants the whole 64 passes ``max_length=64`` and no prefix.
DEFAULT_STEM_LENGTH = 57


def transliterate(text: str) -> str:
    """Fold Cyrillic and accented Latin onto plain ASCII letters."""
    out = []
    for ch in text:
        low = ch.lower()
        if low in CYRILLIC:
            mapped = CYRILLIC[low]
        elif low in LATIN_FOLD:
            mapped = LATIN_FOLD[low]
        else:
            out.append(ch)
            continue
        out.append(mapped.upper() if ch != low and mapped else mapped)
    return "".join(out)


def slugify(text: str, max_length: int = 128) -> str:
    """``[a-z0-9-]`` slug: transliterate, lowercase, collapse the rest to ``-``."""
    s = _NON_SLUG.sub("-", transliterate(text or "").lower()).strip("-")
    if len(s) > max_length:
        s = s[:max_length].rstrip("-")
    return s


def feature_slug(tag: str) -> str:
    """``[a-z0-9_]`` snake_case slug for a source field tag.

    ``WholesaleMinOrderType`` -> ``wholesale_min_order_type``; a non-Latin tag
    is transliterated first.
    """
    s = transliterate(tag or "")
    s = _CAMEL_2.sub("_", _CAMEL_1.sub("_", s)).lower()
    s = _NON_FEATURE.sub("_", s).strip("_")
    return s


def vocabulary_slug(basename: str, prefix: str = "",
                    max_length: int = DEFAULT_STEM_LENGTH) -> str:
    """``phone_catalog.xml`` -> ``phone-catalog`` (``<prefix>-phone-catalog``).

    *prefix* namespaces one source's vocabularies inside a fleet that imports
    from more than one: two sources both shipping a ``brands.xml`` must not
    fight over the slug ``brands``, because the slug is the vocabulary's
    identity. *max_length* is the budget for the stem alone, so the prefix
    never pushes the result past the schema's 64-character cap.
    """
    stem = basename[:-4] if basename.lower().endswith(".xml") else basename
    body = slugify(stem, max_length=max_length)
    return "%s-%s" % (prefix, body) if prefix else body


def path_hash(parts) -> str:
    """First 8 hex of the sha1 of a category path — the over-long-slug tail."""
    blob = "/".join(parts).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:8]


def dedup(codes):
    """Append ``-2``, ``-3``… to repeated codes, in the order given.

    Callers feed the codes in the order the fixture will carry them (labels
    sorted), so the suffix a term gets is a property of the data, not of the
    iteration order of a dict.

    The suffix skips anything already spoken for — both a code handed out
    earlier in this call and a code that some *other* label slugifies to on its
    own, anywhere in the list. Without that second reservation the suffix
    collides with the data: a catalogue shipping the trim levels ``Exclusive``,
    ``Exclusive 2`` and ``Exclusive+`` slugifies them to ``exclusive``,
    ``exclusive-2`` and ``exclusive`` — a blind ``-2`` on the third hands
    ``exclusive-2`` to two different terms, and a term code is an identity.
    """
    reserved = set(codes)
    assigned = set()
    out = []
    for code in codes:
        if code not in assigned:
            assigned.add(code)
            out.append(code)
            continue
        suffix = 2
        candidate = "%s-%d" % (code, suffix)
        while candidate in reserved or candidate in assigned:
            suffix += 1
            candidate = "%s-%d" % (code, suffix)
        assigned.add(candidate)
        out.append(candidate)
    return out
