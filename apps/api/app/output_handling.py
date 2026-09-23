"""Week 6: output-side defense. The reply is the last untrusted surface.

EchoLeak (CVE-2025-32711) exfiltrated through the model's OUTPUT: a rendered
image or link whose URL carries data. Input defenses cannot see that; this runs
on whatever the model returns, before it is stored or sent to any client.

Policy:
- Images never survive. A rendered image is a zero-click network request to an
  attacker-chosen host; there is no legitimate need for the model to emit one.
- A link stays clickable only if its exact URL already appeared in the retrieved
  chunks this request was shown. Such a URL carries no information the org's
  documents did not already contain, so it cannot exfiltrate; anything the model
  constructed, completed, or assembled is de-fanged to visible inert text.
- Non-http(s) schemes never survive.
- Every decision is reported to the caller for the audit log: a stripped exfil
  link is a detection signal, not just a block.

De-fanging (hxxps://host[.]tld) keeps the URL human-readable for the user and
the audit trail while making it non-clickable and non-autolinkable.
"""

import re
from dataclasses import dataclass, field

from .provenance import RetrievedChunk

# Backticks and emphasis markers are markdown formatting around a URL, not part
# of it; capturing them made exact-match fail for code-formatted allowed URLs.
_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_REF_USE = re.compile(r"\[([^\]]+)\]\[([^\]]+)\]")
_REF_DEF = re.compile(r"^[ ]{0,3}\[([^\]]+)\]:\s*(\S+)[^\n]*$", re.MULTILINE)
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_TRAILING_PUNCT = ".,;:!?'\"*_~"
_SAFE_SCHEMES = ("http://", "https://")


@dataclass
class SanitizedOutput:
    text: str
    blocked_images: list[str] = field(default_factory=list)
    defanged_links: list[str] = field(default_factory=list)
    allowed_links: list[str] = field(default_factory=list)

    @property
    def actions(self) -> dict:
        # Shape stored on the audit record for this model call.
        return {
            "blocked_images": self.blocked_images,
            "defanged_links": self.defanged_links,
            "allowed_links": self.allowed_links,
        }


def defang(url: str) -> str:
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    return f"{scheme.replace('http', 'hxxp')}://{host.replace('.', '[.]')}{slash}{path}"


def allowed_urls_from_chunks(chunks: list[RetrievedChunk]) -> set[str]:
    """Exact URLs present in the retrieved text: the grant is what a document
    literally says, never what a model assembles from it."""
    urls = set()
    for chunk in chunks:
        for match in _URL.findall(chunk.content):
            urls.add(match.rstrip(_TRAILING_PUNCT))
    return urls


def sanitize_output(text: str, allowed_urls: set[str]) -> SanitizedOutput:
    result = SanitizedOutput(text="")
    placeholders: list[str] = []

    def _hold(verbatim: str) -> str:
        placeholders.append(verbatim)
        return f"\x00{len(placeholders) - 1}\x00"

    # Reference-style links become inline so one policy covers every form; the
    # definition lines are consumed in the process.
    definitions = {label.lower(): url for label, url in _REF_DEF.findall(text)}
    text = _REF_DEF.sub("", text)
    text = _REF_USE.sub(
        lambda m: f"[{m.group(1)}]({definitions[m.group(2).lower()]})"
        if m.group(2).lower() in definitions
        else m.group(0),
        text,
    )

    def _image(m: re.Match) -> str:
        url = m.group(2)
        result.blocked_images.append(url)
        shown = defang(url) if url.startswith(_SAFE_SCHEMES) else "unsafe scheme"
        return f"[image removed: {shown}]"

    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if not url.startswith(_SAFE_SCHEMES):
            result.defanged_links.append(url)
            return label
        if url in allowed_urls:
            result.allowed_links.append(url)
            return _hold(f"[{label}]({url})")
        result.defanged_links.append(url)
        return f"{label} ({defang(url)})"

    def _autolink(m: re.Match) -> str:
        url = m.group(1)
        if url in allowed_urls:
            result.allowed_links.append(url)
            return _hold(m.group(0))
        result.defanged_links.append(url)
        return defang(url)

    def _bare(m: re.Match) -> str:
        url = m.group(0)
        trailing = ""
        stripped = url.rstrip(_TRAILING_PUNCT)
        trailing = url[len(stripped) :]
        if stripped in allowed_urls:
            result.allowed_links.append(stripped)
            return _hold(stripped) + trailing
        result.defanged_links.append(stripped)
        return defang(stripped) + trailing

    text = _IMAGE.sub(_image, text)
    text = _LINK.sub(_link, text)
    text = _AUTOLINK.sub(_autolink, text)
    text = _URL.sub(_bare, text)

    for i, verbatim in enumerate(placeholders):
        text = text.replace(f"\x00{i}\x00", verbatim)
    result.text = text
    return result
