"""Header values encoded and folded here, not by the stdlib policy.

The stdlib folds a long non-ASCII header into several RFC 2047 encoded words.
On Python 3.9 (and some later 3.x) it puts the space between two words
*between* encoded words, where RFC 2047 section 6.2 tells a decoder to drop
it, so "kasvoi Pääkaupunkiseutu" reads "kasvoiPääkaupunkiseutu" in every
client. Here the text is cut into encoded words with every space *inside*
the encoded text, and the header is folded only between whole tokens, so
decoding gives back exactly the text given, on every Python.

:class:`Folded` is a ``str`` (the unfolded header value, which the stdlib
and ``smtplib`` read as usual) with the ``name`` and ``fold`` the email
policy uses for a header object, so it is written out as built here.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence

#: Longest encoded word written (RFC 2047 allows 75); with "Subject: " in
#: front the first line stays within 76 characters.
MAX_WORD = 64
#: Line length the header is folded to (RFC 2045's 76, as the bodies).
MAX_LINE = 76
_PREFIX = "=?utf-8?b?"
#: ASCII text that needs no quoting in a display name (RFC 5322 atext + space).
_ATEXT = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~ -]*")


class Folded(str):
    """A header value encoded and folded by this module (see the module
    docstring). ``addresses`` mirrors the stdlib address header attribute."""

    def __new__(cls, name: str, tokens: Sequence[str], addresses: tuple = ()) -> Folded:
        obj = super().__new__(cls, " ".join(tokens))
        obj.name, obj.tokens, obj.addresses = name, list(tokens), addresses
        return obj

    def fold(self, *, policy) -> str:
        lines, line = [], f"{self.name}:"
        for tok in self.tokens:
            if len(line) + 1 + len(tok) > MAX_LINE and line != f"{self.name}:":
                lines.append(line)
                line = ""
            line += " " + tok
        lines.append(line)
        return policy.linesep.join(lines) + policy.linesep


def encoded_words(text: str) -> list[str]:
    """``text`` as base64 UTF-8 encoded words of at most :data:`MAX_WORD`
    characters, spaces included in the encoded text (a decoder drops the
    whitespace *between* encoded words), cut after a word's trailing spaces
    where possible and between characters (never inside one) otherwise."""
    words, chunk = [], ""
    for part in re.findall(r"\S+\s*|\s+", text):  # a word with the spaces after it
        if chunk and _word_len(chunk + part) > MAX_WORD:
            words.append(_word(chunk))
            chunk = ""
        for ch in part:  # a word longer than one encoded word is cut between characters
            if chunk and _word_len(chunk + ch) > MAX_WORD:
                words.append(_word(chunk))
                chunk = ""
            chunk += ch
    if chunk:
        words.append(_word(chunk))
    return words


def _word(chunk: str) -> str:
    return _PREFIX + base64.b64encode(chunk.encode("utf-8")).decode("ascii") + "?="


def _word_len(chunk: str) -> int:
    return len(_PREFIX) + 2 + 4 * -(-len(chunk.encode("utf-8")) // 3)


def text_tokens(text: str) -> list[str]:
    """Tokens of an unstructured header (Subject): printable ASCII as words
    (folded at its spaces), anything else as encoded words."""
    if text.isascii() and text.isprintable() and all(len(w) <= MAX_WORD for w in text.split()):
        return text.split(" ")
    return encoded_words(text)


def mailbox_tokens(display_name: str, addr_spec: str) -> list[str]:
    """Tokens of one mailbox: the display name (an atom phrase, a quoted
    string, or encoded words) and ``<addr_spec>``."""
    if not display_name:
        return [addr_spec]
    if display_name.isascii() and display_name.isprintable():
        words = display_name.split(" ")
        if _ATEXT.fullmatch(display_name) and all(0 < len(w) <= MAX_LINE - 6 for w in words):
            return [*words, f"<{addr_spec}>"]  # an atom phrase, folded at its spaces
        if len(display_name) <= MAX_WORD - 2:
            quoted = display_name.replace("\\", "\\\\").replace('"', '\\"')
            return [f'"{quoted}"', f"<{addr_spec}>"]
    return [*encoded_words(display_name), f"<{addr_spec}>"]


def address_header(name: str, mailboxes: Sequence[tuple[str, str]], addresses: tuple
                   ) -> Folded:
    tokens: list[str] = []
    for i, (display, spec) in enumerate(mailboxes):
        toks = mailbox_tokens(display, spec)
        if i < len(mailboxes) - 1:
            toks[-1] += ","
        tokens += toks
    return Folded(name, tokens, addresses)
