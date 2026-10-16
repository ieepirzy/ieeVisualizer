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
caller passes ``allow_ungated=True``. Design notes: ``docs/MOBILE_AND_EMAIL.md``,
"Sending a briefing".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Union

from ..render import LegibilityError, check_legible
from ..spec import SpecError, ValidationIssue
from .email import EmailRender

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
                  allow_ungated: bool = False, date: str | None = None,
                  message_id: str | None = None) -> EmailMessage:
    """The message for ``email`` (see the module docstring).

    ``text_fallback`` replaces the generated plain-text part
    (``email.text``). ``date`` (an RFC 5322 date) and ``message_id`` default
    to now and a fresh id in the sender's domain. Raises
    :class:`EmailGateError` for a render the gate refuses unless
    ``allow_ungated=True``, :class:`MessageError` for anything else wrong."""
    if not allow_ungated:
        try:
            check_legible(email)
        except LegibilityError as exc:
            raise EmailGateError(exc.issues) from None
    issues: list[ValidationIssue] = []
    subject = _header_text(subject, "/subject", issues)
    from_ = _address(sender, "/sender", issues)
    if isinstance(recipients, (str, Address)):
        recipients = [recipients]
    to = [_address(r, f"/recipients/{i}", issues) for i, r in enumerate(recipients)]
    if not to:
        issues.append(ValidationIssue("/recipients", "no_recipients",
                                      "give at least one recipient."))
    for value, where in ((date, "/date"), (message_id, "/message_id")):
        if value is not None:
            _header_text(value, where, issues)
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
    msg["Subject"] = subject
    msg["From"] = from_
    msg["To"] = to
    msg["Date"] = date or formatdate(localtime=False)
    msg["Message-ID"] = message_id or make_msgid(domain=from_.domain)
    msg.set_content(_crlf(text), charset="utf-8", cte="quoted-printable")
    msg.add_alternative(_crlf(email.html), subtype="html", charset="utf-8",
                        cte="quoted-printable")
    html_part = msg.get_payload()[1]
    for name, content in email.images.items():
        html_part.add_related(content, maintype="image", subtype="png", cid=f"<{name}>",
                              disposition="inline", filename=name.split("@", 1)[0])
    return msg


def write_eml(message: EmailMessage, path: str | Path) -> Path:
    """Write ``message`` as an ``.eml`` file (CRLF line ends, as sent) that a
    mail client opens as a received message; returns the path."""
    path = Path(path)
    path.write_bytes(message.as_bytes(policy=POLICY))
    return path


def _crlf(text: str) -> str:
    """Line ends normalised to ``\\n`` (the policy writes CRLF on output)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _header_text(value: object, path: str, issues: list[ValidationIssue]) -> str:
    if not isinstance(value, str):
        issues.append(ValidationIssue(path, "invalid_type", "must be a string."))
        return ""
    if _CONTROL.search(value):
        issues.append(ValidationIssue(path, "header_injection", (
            "contains a line break or control character; a header value must be one "
            f"line: {value[:60]!r}.")))
    elif not value.strip():
        issues.append(ValidationIssue(path, "empty_header", "must not be empty."))
    elif len(value) > MAX_SUBJECT:
        issues.append(ValidationIssue(path, "header_too_long",
                                      f"{len(value)} characters; at most {MAX_SUBJECT}."))
    return value.strip()


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
    if not _ADDR.fullmatch(addr):
        issues.append(ValidationIssue(path, "invalid_address", (
            f"{addr[:60]!r} is not a plain local@domain address (ASCII; one '@'; a dotted "
            "domain).")))
        return Address()
    local, domain = addr.rsplit("@", 1)
    return Address(display_name=name, username=local, domain=domain)
