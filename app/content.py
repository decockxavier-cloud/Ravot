"""Veilige Markdown → HTML voor bewerkbare inhoudspagina's en mails.

De admin bewerkt Markdown (geen rauwe HTML). Bij het tonen zetten we het om
naar HTML en schonen we het op met bleach, zodat er nooit kwaadaardige HTML
(scripts, iframes...) bij de bezoeker terechtkomt — ook niet per ongeluk.
"""
import markdown as _md
import bleach

_TAGS = ["h1", "h2", "h3", "h4", "p", "strong", "em", "b", "i", "u",
         "a", "ul", "ol", "li", "br", "blockquote", "hr", "code", "pre"]
_ATTRS = {"a": ["href", "title"]}


def render_markdown(tekst):
    """Markdown-string → veilige HTML-string."""
    if not tekst:
        return ""
    html = _md.markdown(tekst, extensions=["nl2br"])
    schoon = bleach.clean(html, tags=_TAGS, attributes=_ATTRS, strip=True)
    return bleach.linkify(schoon)
