"""An email message from an :class:`~ieeVisualizer.view.email.EmailRender`.

``build_message(email, subject=..., sender=..., recipients=[...])`` returns a
stdlib :class:`email.message.EmailMessage` ready for ``smtplib.send_message``::

    multipart/alternative
      text/plain; charset=utf-8          EmailRender.text (or text_fallback)
      multipart/related
        text/html; charset=utf-8         EmailRender.html
        image/png  (one per image)       Content-ID <name>, inline, filename

Each image is attached exactly once, under the Content-ID its ``cid:``
reference in the HTML names; a reference without an image, or an image
the HTML does not reference, is refused (``cid_mismatch``) - the first
would show as a broken image, the second as a stray attachment. Text parts
are quoted-printable and images base64, so no line exceeds 76 characters
whatever the content. Subject and addresses are checked before anything is
built: a CR, LF or other control character (header injection), an address
that is not exactly one ``local@domain``, or an empty recipient list is a
:class:`MessageError` with a pointer to the field.

The send gate is applied here too: an ``EmailRender`` that
:func:`~ieeVisualizer.render.check_legible` refuses raises
:class:`EmailGateError` (its ``codes`` list the gating warnings) unless the
caller passes ``allow_ungated=True``. So does one not laid out for a phone,
one changed after ``render_email`` made it (``EmailRender.seal``: HTML,
text, images, profile and the gate's verdict), one built by hand, and a
caller's ``text_fallback`` (it could say anything): each a
:class:`MessageError` naming what is wrong.

Headers are built from :class:`~email.headerregistry.Address` objects and
plain strings this module has checked; a display name or subject holding
RFC 2047 encoded-word syntax (``=?...?=``) is refused, because the stdlib
decodes it while folding and would write what it decodes to - another
address, a line break - into the header. Design notes: ``docs/MOBILE_AND_EMAIL.md``,
"Sending a briefing".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from pathlib import Path
from typing import Union

from ..render import LegibilityError, check_legible, get_profile
from ..spec import SpecError, ValidationIssue
from ._headers import Folded, address_header, text_tokens
from .email import EmailRender, seal

#: The HTML's references to its images (``render_email`` writes ``src="cid:..."``).
_CID = re.compile(r'src="cid:([^"]*)"')
#: Control characters (C0, DEL, C1) and the Unicode line/paragraph separators:
#: none belongs in a header value, and CR/LF would start a new header.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]")
#: ``local@domain`` with a dot-atom local part and an ASCII DNS domain.
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_ADDR = re.compile(rf"{_ATOM}(\.{_ATOM})*@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
                   r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+")
#: A Content-ID / Message-ID body (RFC 5322 msg-id without the brackets).
_MSG_ID = re.compile(rf"{_ATOM}(\.{_ATOM})*@{_ATOM}(\.{_ATOM})*")
#: RFC 2047 encoded-word syntax: the stdlib decodes it inside header values.
_ENCODED_WORD = re.compile(r"=\?.*\?=", re.S)
#: Hangul fillers: letters that draw nothing (a "name" of them shows blank).
_FILLERS = "\u115f\u1160\u3164\uffa0"
#: RFC 5321 length limits: local part, domain, one domain label.
MAX_LOCAL, MAX_DOMAIN, MAX_LABEL = 64, 253, 63
#: ``Display Name <addr>`` or a bare ``addr``.
_NAMED = re.compile(r"\s*(?:(?P<name>[^<>]*?)\s*<(?P<a>[^<>]*)>|(?P<bare>[^<>\s]+))\s*")
#: RFC 2045 limits encoded lines to 76 characters; the stdlib policy wraps
#: quoted-printable bodies (and folds headers) at ``max_line_length``.
POLICY = policy.SMTP.clone(max_line_length=76)
#: A subject longer than this is surely a mistake (a body pasted in).
MAX_SUBJECT = 250

AddressLike = Union[str, Address]


class MessageError(SpecError):
    """The message cannot be built as asked (a header value, an address,
    the images). ``issues`` point at the argument: ``/subject``,
    ``/sender``, ``/recipients/<i>``, ``/images/<name>``."""


class EmailGateError(LegibilityError):
    """The send gate refused the render; ``codes`` are the gating warning codes."""

    @property
    def codes(self) -> list[str]:
        return sorted({i.code for i in self.issues})


def build_message(email: EmailRender, *, subject: str, sender: AddressLike,
                  recipients: Iterable[AddressLike], text_fallback: str | None = None,
                  allow_ungated: bool = False, allow_custom_text: bool = False,
                  date: str | None = None, message_id: str | None = None) -> EmailMessage:
    """The message for ``email`` (see the module docstring).

    ``text_fallback`` replaces the generated plain-text part
    (``email.text``) and needs ``allow_custom_text=True`` (the gate, seal and
    profile checks still apply). ``date`` (an RFC 5322
    date with a time zone) and ``message_id`` (``<id@domain>``) default to
    now in GMT and a fresh id in the sender's domain. Raises
    :class:`EmailGateError` for a render the gate refuses unless
    ``allow_ungated=True``, :class:`MessageError` for anything else wrong."""
    if text_fallback is not None and not allow_custom_text:
        raise MessageError([ValidationIssue("/text_fallback", "custom_text_not_allowed", (
            "text_fallback replaces the plain text generated from the view, so nothing "
            "checks that it says what the email shows; pass allow_custom_text=True to "
            "send it anyway."))])
    if not allow_ungated:
        problems = _ungated(email)
        if problems:
            raise MessageError(problems)
        try:
            check_legible(email)
        except LegibilityError as exc:
            raise EmailGateError(exc.issues) from None
        if not _phone_safe(email.profile):  # sealed renders say so above; never rely on it
            raise MessageError([ValidationIssue("/profile", "not_phone_safe", (
                f"rendered for {email.profile!r}, not a phone profile; render with "
                "'email-phone' for email anyone may read on a phone."))])
    issues: list[ValidationIssue] = []
    subject = _header_text(subject, "/subject", issues)
    from_ = _address(sender, "/sender", issues)
    if isinstance(recipients, (str, Address)):
        recipients = [recipients]
    to = [_address(r, f"/recipients/{i}", issues) for i, r in enumerate(recipients)]
    if not to:
        issues.append(ValidationIssue("/recipients", "no_recipients",
                                      "give at least one recipient."))
    if date is not None and _header_text(date, "/date", issues, limit=False):
        try:
            when = parsedate_to_datetime(date)
        except (TypeError, ValueError, IndexError):
            when = None
        if when is None or when.tzinfo is None:
            issues.append(ValidationIssue("/date", "invalid_date", (
                f"{date[:60]!r} is not an RFC 5322 date with a time zone, e.g. "
                "'Fri, 16 Oct 2026 09:00:00 +0300'.")))
    if message_id is not None and _header_text(message_id, "/message_id", issues,
                                               limit=False):
        if not (message_id.startswith("<") and message_id.endswith(">")
                and _MSG_ID.fullmatch(message_id[1:-1])):
            issues.append(ValidationIssue("/message_id", "invalid_message_id", (
                f"{message_id[:60]!r} is not a Message-ID: '<id@domain>' in dot-atoms.")))
    if text_fallback is not None and not isinstance(text_fallback, str):
        issues.append(ValidationIssue("/text_fallback", "invalid_type",
                                      "text_fallback must be a string."))
    text = email.text if text_fallback is None else text_fallback
    if not (text or "").strip():
        issues.append(ValidationIssue("/text_fallback", "empty_text", (
            "the plain-text part would be empty: a client that shows no HTML would show "
            "nothing. Render with render_email (it fills EmailRender.text) or pass "
            "text_fallback.")))
    refs = _CID.findall(email.html)
    for name in sorted(set(refs) - set(email.images)):
        issues.append(ValidationIssue(f"/images/{name}", "cid_mismatch", (
            f"the HTML references cid:{name} but no image has that name; it would show "
            "as a broken image.")))
    for name in sorted(set(email.images) - set(refs)):
        issues.append(ValidationIssue(f"/images/{name}", "cid_mismatch", (
            f"image {name!r} is not referenced by the HTML as cid:{name}; render with the "
            "default image_src (cid:<name>) for a message.")))
    for name in email.images:
        if not _MSG_ID.fullmatch(name):  # a Content-ID is a msg-id: dot-atom@dot-atom
            issues.append(ValidationIssue(f"/images/{name}", "invalid_content_id",
                                          f"{name!r} cannot be a Content-ID."))
    if issues:
        raise MessageError(issues)

    msg = EmailMessage(policy=POLICY)
    # encoded and folded here (``_headers``): the stdlib's folding drops the
    # spaces between encoded words on some Pythons (3.9)
    msg["Subject"] = Folded("Subject", text_tokens(subject))
    msg["From"] = address_header("From", [_pair(from_)], (from_,))
    msg["To"] = address_header("To", [_pair(a) for a in to], tuple(to))
    msg["Date"] = date or formatdate(usegmt=True)
    msg["Message-ID"] = message_id or make_msgid(domain=from_.domain)
    msg.set_content(_crlf(text), charset="utf-8", cte="quoted-printable")
    msg.add_alternative(_crlf(email.html), subtype="html", charset="utf-8",
                        cte="quoted-printable")
    html_part = msg.get_payload()[1]
    for name, content in email.images.items():
        html_part.add_related(content, maintype="image", subtype="png", cid=f"<{name}>",
                              disposition="inline", filename=name.split("@", 1)[0])
    if email.images:
        html_part.set_param("type", "text/html")  # RFC 2387: the root part's type
    for part in msg.walk():  # "From " starting a line is mangled by mbox tools: =46rom
        if part.get_content_maintype() == "text":
            part.set_payload(_from_lines(part.get_payload()))
    return msg


def _pair(a: Address) -> tuple[str, str]:
    return a.display_name, f"{a.username}@{a.domain}"


def _from_lines(payload: str) -> str:
    """Quoted-printable ``payload`` with every line that starts with "From "
    written "=46rom ", re-wrapped (soft line breaks, never inside an ``=XX``
    escape) so no line exceeds 76 characters."""
    out = []
    for line in payload.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        end = line[len(body):]
        if not body.startswith("From "):
            out.append(line)
            continue
        body = "=46rom " + body[5:]
        soft = body.endswith("=")
        if soft:
            body = body[:-1]
        pieces = []
        while len(body) > 75:  # 75 + the soft-break "=" is 76
            cut = 75
            esc = body.rfind("=", cut - 2, cut)  # an escape cut in two?
            if esc != -1:
                cut = esc
            pieces.append(body[:cut] + "=")
            body = body[cut:]
        pieces.append(body + ("=" if soft else ""))
        out.append("\n".join(pieces) + end)
    return "".join(out)


def _phone_safe(profile: str) -> bool:
    try:
        return bool(get_profile(profile).narrow)
    except Exception:  # noqa: BLE001 - an unknown profile is not phone-safe
        return False


def _ungated(email: EmailRender) -> list[ValidationIssue]:
    """What makes ``email`` unfit to send through the gate, besides its
    warnings: not made by ``render_email``, or changed since. (Not laid out
    for a phone is checked after the gate.)"""
    if not isinstance(email, EmailRender) or not email.seal:
        return [ValidationIssue("", "not_rendered", (
            "this EmailRender was not made by render_email (it carries no seal), so the "
            "gate's verdict is not known for it. Render the view with render_email, or "
            "pass allow_ungated=True."))]
    try:
        now = seal(email)
    except (AttributeError, TypeError, UnicodeError) as exc:
        now = {"error": str(exc)}
    out = []
    for key in sorted(set(email.seal) | set(now)):
        if email.seal.get(key) != now.get(key):
            out.append(ValidationIssue(f"/{key}", "render_modified", (
                f"EmailRender.{key} changed after render_email made it; the gate's verdict "
                "was for what it made. Render again instead of editing the result.")))
    return out


def write_eml(message: EmailMessage, path: str | Path) -> Path:
    """Write ``message`` as an ``.eml`` file (CRLF line ends, as sent) that a
    mail client opens as a received message; returns the path."""
    path = Path(path)
    path.write_bytes(message.as_bytes(policy=POLICY))
    return path


def _crlf(text: str) -> str:
    """Line ends normalised to ``\\n`` (the policy writes CRLF on output)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _header_text(value: object, path: str, issues: list[ValidationIssue],
                 limit: bool = True) -> str:
    """``value`` stripped, or ``""`` with an issue added when it cannot be a
    header value."""
    n = len(issues)
    if not isinstance(value, str):
        issues.append(ValidationIssue(path, "invalid_type", "must be a string."))
        return ""
    if _CONTROL.search(value):
        issues.append(ValidationIssue(path, "header_injection", (
            "contains a line break or control character; a header value must be one "
            f"line: {value[:60]!r}.")))
    elif not value.strip():
        issues.append(ValidationIssue(path, "empty_header", "must not be empty."))
    elif limit and len(value) > MAX_SUBJECT:
        issues.append(ValidationIssue(path, "header_too_long",
                                      f"{len(value)} characters; at most {MAX_SUBJECT}."))
    else:
        _words(value, path, issues)
    return value.strip() if len(issues) == n else ""


def _words(value: str, path: str, issues: list[ValidationIssue]) -> None:
    """Text that is shown in a header: no encoded words, no format characters."""
    if _ENCODED_WORD.search(value):
        issues.append(ValidationIssue(path, "encoded_word", (
            "contains RFC 2047 encoded-word syntax ('=?...?='); mail software decodes it, "
            "so it could carry another address or a line break. Give the plain text; it "
            f"is encoded for you: {value[:60]!r}.")))
    fmt = sorted({f"U+{ord(c):04X}" for c in value if unicodedata.category(c) == "Cf"})
    if fmt:
        issues.append(ValidationIssue(path, "format_character", (
            f"contains invisible format characters ({', '.join(fmt[:5])}: bidi overrides, "
            "zero-width characters), which can make a header read as something else.")))


def _address(value: object, path: str, issues: list[ValidationIssue]) -> Address:
    """One mailbox from ``"Name <a@b.example>"``, ``"a@b.example"`` or an
    :class:`Address`; anything else is an issue (never a guess)."""
    if isinstance(value, Address):
        name, addr = value.display_name, value.addr_spec
    elif isinstance(value, str):
        if _CONTROL.search(value):
            issues.append(ValidationIssue(path, "header_injection", (
                "contains a line break or control character; an address must be one line: "
                f"{value[:60]!r}.")))
            return Address()
        m = _NAMED.fullmatch(value)
        if m is None:
            issues.append(ValidationIssue(path, "invalid_address", (
                f"{value[:60]!r} is not one address; give 'Name <local@domain>' or "
                "'local@domain', one per list entry.")))
            return Address()
        name, addr = (m["name"] or "").strip().strip('"'), (m["a"] or m["bare"]).strip()
    else:
        issues.append(ValidationIssue(path, "invalid_type", "must be a string or an Address."))
        return Address()
    if _CONTROL.search(name) or _CONTROL.search(addr):
        issues.append(ValidationIssue(path, "header_injection",
                                      "contains a line break or control character."))
        return Address()
    n = len(issues)
    _words(name, path, issues)
    shown = unicodedata.normalize("NFKC", name)  # as it reads: ＠ is @, ＜ is <
    if "@" in shown or "<" in shown or ">" in shown:
        issues.append(ValidationIssue(path, "address_in_display_name", (
            f"the display name {name[:60]!r} looks like an address; a reader would take it "
            "for the sender or recipient. Use a plain name.")))
    elif name and not "".join(c for c in shown if not c.isspace() and c not in _FILLERS):
        issues.append(ValidationIssue(path, "blank_display_name", (
            f"the display name {name!r} shows as blank; give a visible name or none.")))
    if len(issues) > n:
        return Address()
    addr = _ascii_domain(addr)
    local, _, domain = addr.rpartition("@")
    labels = domain.split(".")
    if (not _ADDR.fullmatch(addr) or "=?" in addr or len(local) > MAX_LOCAL
            or len(domain) > MAX_DOMAIN or max(map(len, labels)) > MAX_LABEL):
        issues.append(ValidationIssue(path, "invalid_address", (
            f"{addr[:60]!r} is not a plain local@domain address (ASCII local part of at "
            f"most {MAX_LOCAL} characters, no '=?'; one '@'; a dotted domain of at most "
            f"{MAX_DOMAIN} characters, labels of at most {MAX_LABEL}).")))
        return Address()
    try:
        return Address(display_name=name, username=local, domain=domain)
    except Exception as exc:  # noqa: BLE001 - the stdlib's own parse defects
        issues.append(ValidationIssue(path, "invalid_address", f"{addr[:60]!r}: {exc}"))
        return Address()


def _ascii_domain(addr: str) -> str:
    """``addr`` with an internationalised domain written in IDNA (punycode);
    unchanged when it is ASCII or cannot be encoded (then it is refused)."""
    local, at, domain = addr.rpartition("@")
    if not at or domain.isascii():
        return addr
    try:
        return f"{local}@{domain.encode('idna').decode('ascii')}"
    except UnicodeError:
        return addr
