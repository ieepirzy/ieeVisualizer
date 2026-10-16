"""Header values encoded and folded here, not by the stdlib policy.

The stdlib folds a long non-ASCII header into several RFC 2047 encoded
words. On Python 3.9 it puts the space between two words *between* encoded
words, where RFC 2047 section 6.2 tells a decoder to drop it, so "kasvoi
Pääkaupunkiseutu" reads "kasvoiPääkaupunkiseutu" in a correct decoder.

Here a value is cut into segments at its spaces: an ASCII word is written as
itself (an atom, or a quoted string in a display name), and each run of
consecutive non-ASCII words becomes as few encoded words as fit, with the
spaces inside the run inside the encoded text. The whitespace between an
encoded word and plain text is kept by every decoder, so a space is never
lost. A run is split into several encoded words only when it exceeds the
75-character limit of one (at a space where possible): a correct decoder
(Node's mailparser, Python 3.13, and as far as their behaviour is known
Gmail, Outlook and Apple Mail) joins them exactly; a reader that wrongly
keeps the whitespace between adjacent encoded words (Python 3.9-3.12's
default-policy parser) shows one extra space there, never fewer.

:class:`Folded` is a ``str`` (the unfolded header value, which the stdlib
and ``smtplib`` read as usual) with the ``name`` and ``fold`` the email
policy uses for a header object, so it is written out as built here.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence

#: Longest encoded word (RFC 2047 section 2).
MAX_WORD = 75
#: Line length the header is folded to (RFC 2045's 76, as the bodies); a
#: continuation line is one space plus at most one encoded word.
MAX_LINE = 76
_PREFIX = "=?utf-8?b?"
#: ASCII that is an atom in a display name (RFC 5322 atext).
_ATEXT = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+")


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


def _word(chunk: str) -> str:
    return _PREFIX + base64.b64encode(chunk.encode("utf-8")).decode("ascii") + "?="


def _word_len(chunk: str) -> int:
    return len(_PREFIX) + 2 + 4 * -(-len(chunk.encode("utf-8")) // 3)


def encoded_words(text: str, first: int = MAX_WORD) -> list[str]:
    """``text`` (spaces included) as as few encoded words as fit
    :data:`MAX_WORD` (the first one ``first``, the room left on the header's
    first line): cut after a word's trailing spaces where possible, between
    characters (never inside one) only for a word too long alone."""
    words, chunk = [], ""

    def room() -> int:
        return first if not words else MAX_WORD

    for part in re.findall(r"[^ \t]+[ \t]*|[ \t]+", text):
        if chunk and _word_len(chunk + part) > room():
            words.append(_word(chunk))
            chunk = ""
        for ch in part:
            if chunk and _word_len(chunk + ch) > room():
                words.append(_word(chunk))
                chunk = ""
            chunk += ch
    if chunk:
        words.append(_word(chunk))
    return words


def _segments(text: str, plain, first: int) -> list[str]:
    """Tokens for ``text``: words for which ``plain(word)`` holds stay as
    they are; each run of the others (with the spaces between them) becomes
    encoded words. Spaces beyond the one a token separator stands for go
    into an adjacent encoded run, or after a plain word."""
    parts = re.findall(r"([^ \t]+)([ \t]*)", text.strip(" \t"))  # ASCII spaces only
    tokens: list[str] = []
    run = ""  # the pending non-plain run, spaces included
    for k, (word, gap) in enumerate(parts):
        last = k == len(parts) - 1
        if plain(word):
            if run:
                tokens += encoded_words(run, first if not tokens else MAX_WORD)
                run = ""
            nxt_plain = not last and plain(parts[k + 1][0])
            extra = gap[1:]
            if extra and not nxt_plain and not last:
                run = extra  # carried into the next encoded run
                extra = ""
            tokens.append(word + extra)
        else:
            if last:
                run += word
            elif plain(parts[k + 1][0]):
                run += word + gap[1:]  # one space is the separator
            else:
                run += word + gap
    if run:
        tokens += encoded_words(run, first if not tokens else MAX_WORD)
    return tokens


def text_tokens(text: str, name: str = "Subject") -> list[str]:
    """Tokens of an unstructured header (Subject)."""
    return _segments(text, lambda w: w.isascii() and w.isprintable()
                     and len(w) <= MAX_LINE - len(name) - 3, MAX_LINE - len(name) - 2)


def mailbox_tokens(display_name: str, addr_spec: str, first: int = MAX_WORD) -> list[str]:
    """Tokens of one mailbox: the display name (an atom phrase, a quoted
    string, or atoms and encoded words) and ``<addr_spec>``."""
    if not display_name:
        return [addr_spec]
    if display_name.isascii() and display_name.isprintable():
        words = display_name.split(" ")
        if all(_ATEXT.fullmatch(w) and len(w) <= first for w in words):
            return [*words, f"<{addr_spec}>"]  # an atom phrase, folded at its spaces
        if len(display_name) <= first - 2:
            quoted = display_name.replace("\\", "\\\\").replace('"', '\\"')
            return [f'"{quoted}"', f"<{addr_spec}>"]
    # a phrase may mix atoms and encoded words (RFC 2047 section 5 (3)); an
    # ASCII word with specials ("J.", "Smith,") goes into an encoded run
    return [*_segments(display_name, lambda w: bool(_ATEXT.fullmatch(w)) and len(w) <= first,
                       first), f"<{addr_spec}>"]


def address_header(name: str, mailboxes: Sequence[tuple[str, str]], addresses: tuple
                   ) -> Folded:
    tokens: list[str] = []
    for i, (display, spec) in enumerate(mailboxes):
        toks = mailbox_tokens(display, spec, MAX_LINE - len(name) - 2 if i == 0 else MAX_WORD)
        if i < len(mailboxes) - 1:
            toks[-1] += ","
        tokens += toks
    return Folded(name, tokens, addresses)
