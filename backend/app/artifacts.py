import re
from html import escape

import bleach
import tinycss2
from bleach.css_sanitizer import CSSSanitizer
from bs4 import BeautifulSoup

ALLOWED_TAGS = set(
    "div span p h1 h2 h3 h4 h5 h6 a ul ol li blockquote pre code strong em b i br hr section article header footer main aside nav table thead tbody tr th td caption figure figcaption small sup sub details summary".split()
)
CSS_PROPERTIES = set(
    "color background-color font-family font-size font-weight font-style line-height letter-spacing text-align text-decoration text-transform white-space display position top left right bottom width max-width min-width height min-height max-height margin margin-top margin-right margin-bottom margin-left padding padding-top padding-right padding-bottom padding-left border border-width border-style border-color border-radius border-bottom border-top box-shadow box-sizing opacity overflow overflow-wrap word-break grid-template-columns grid-template-rows gap row-gap column-gap align-items justify-content flex flex-direction flex-wrap flex-grow flex-basis list-style-type vertical-align".split()
)
CSS = CSSSanitizer(allowed_css_properties=CSS_PROPERTIES)
CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'"


def safe_declarations(raw: str) -> str:
    # Restrict values as well as properties. Block all network-bearing and legacy execution syntax.
    declarations = []
    for d in tinycss2.parse_declaration_list(raw, skip_comments=True, skip_whitespace=True):
        if d.type != "declaration" or d.lower_name not in CSS_PROPERTIES:
            continue
        value = tinycss2.serialize(d.value)
        if re.search(r"url\s*\(|expression\s*\(|@|\\|[<>]", value, re.I):
            continue
        declarations.append(f"{d.lower_name}:{value}")
    return ";".join(declarations)


def safe_stylesheet(raw: str) -> str:
    rules = []
    for rule in tinycss2.parse_stylesheet(raw, skip_comments=True, skip_whitespace=True):
        if rule.type != "qualified-rule":
            continue  # all @rules including imports/fonts are blocked
        selector = tinycss2.serialize(rule.prelude)
        if re.search(r"[<>\\@]", selector):
            continue
        declarations = safe_declarations(tinycss2.serialize(rule.content))
        if declarations:
            rules.append(f"{selector}{{{declarations}}}")
    return "\n".join(rules)


def sanitize_html(raw: str) -> str:
    raw = re.sub(r"^```(?:html)?\s*|\s*```$", "", raw.strip())
    soup = BeautifulSoup(raw, "html.parser")
    title_node = soup.title or soup.find("h1")
    title = escape(title_node.get_text(" ", strip=True) if title_node else "Growth document")
    styles = "\n".join(safe_stylesheet(tag.get_text()) for tag in soup.find_all("style"))
    for tag in soup.find_all(
        [
            "script",
            "iframe",
            "object",
            "embed",
            "form",
            "input",
            "button",
            "textarea",
            "select",
            "svg",
            "math",
            "link",
            "meta",
            "base",
            "style",
        ]
    ):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.has_attr("style"):
            tag["style"] = safe_declarations(tag["style"])
        # Links stay within the artifact. Source navigation is owned by the surrounding app.
        if tag.has_attr("href") and not str(tag["href"]).startswith("#"):
            del tag["href"]
    body = bleach.clean(
        str(soup.body or soup),
        tags=ALLOWED_TAGS,
        attributes={
            "*": ["class", "id", "style", "title", "aria-label"],
            "a": ["href"],
            "td": ["colspan", "rowspan"],
            "th": ["scope", "colspan"],
        },
        protocols=[],
        strip=True,
        css_sanitizer=CSS,
    )
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title><meta http-equiv="Content-Security-Policy" content="{CSP}"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{{margin:0;padding:32px;font-family:system-ui;line-height:1.65;overflow-wrap:anywhere}}*{{box-sizing:border-box}}{styles}</style></head><body>{body}</body></html>'


def word_count(content: str) -> int:
    text = BeautifulSoup(content, "html.parser").get_text(" ") if "<html" in content else content
    text = re.sub(r"\[S\d+\]", "", text)
    return len(re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", text))


def citations(content: str) -> set[str]:
    return {
        citation
        for group in re.findall(r"\[(S\d+(?:\s*,\s*S\d+)*)\]", content)
        for citation in re.findall(r"S\d+", group)
    }


def normalize_citations(content: str, allowed: set[str] | None = None) -> str:
    normalized = re.sub(
        r"\[(S\d+(?:\s*,\s*S\d+)+)\]",
        lambda match: "".join(f"[{item}]" for item in re.findall(r"S\d+", match[1])),
        content,
    )
    if allowed:
        # Small models sometimes omit brackets around a supplied ID. Canonicalize
        # only known standalone IDs; unknown tokens remain invalid and citation-only
        # drafts are rejected below.
        normalized = re.sub(
            r"(?<![\w\[])\b(S\d+)\b(?!\])",
            lambda match: f"[{match[1]}]" if match[1] in allowed else match[0],
            normalized,
        )
    return normalized


def validate(content: str, mode: str, evidence: list[dict], *, grounded: bool = True) -> list[str]:
    problems = []
    used = citations(content)
    allowed = {e["citation"] for e in evidence}
    if grounded and (not used or not used <= allowed):
        problems.append("Use only supplied citation IDs [S1], [S2], etc.; cite substantive claims.")
    elif not grounded and used:
        problems.append("No podcast evidence was used. Remove source citations from this original writing task.")
    if grounded and not re.sub(r"\s*(?:\[S\d+\]|S\d+)\s*", "", content).strip():
        problems.append("The draft contains no substantive answer text; return a supported answer or ABSTAIN.")
    # Catch a common small-model error: retaining a percentage while inventing its metric.
    # This is a narrow deterministic check, not a claim-entailment guarantee.
    visible_content = content
    if mode == "html":
        # Layout values such as width:100% are CSS, not numerical claims.
        document = BeautifulSoup(content, "html.parser")
        for node in document.find_all(["style", "script", "head"]):
            node.decompose()
        visible_content = document.get_text(" ", strip=True)
    for paragraph in re.split(r"\n\s*\n", visible_content) if grounded else []:
        referenced = citations(paragraph)
        passages = [e["excerpt"].lower() for e in evidence if e["citation"] in referenced]
        unsupported_values = {
            value
            for value in re.findall(r"\b\d+(?:\.\d+)?%", paragraph)
            if not any(value in passage for passage in passages)
        }
        if unsupported_values:
            problems.append(
                "Remove percentages not explicitly present in the paragraph's cited excerpts: "
                + ", ".join(sorted(unsupported_values))
            )
        for match in re.finditer(r"\b(\d+(?:\.\d+)?%)\s+([a-zA-Z-]+)", paragraph):
            value, measure = match[1], match[2].lower()
            if value in unsupported_values:
                continue
            if measure in {
                "of",
                "or",
                "and",
                "is",
                "was",
                "are",
                "were",
                "in",
                "to",
                "for",
                "by",
                "as",
                "the",
                "a",
                "an",
            }:
                continue
            if passages and not any(
                value in passage and re.search(rf"\b{re.escape(measure)}\b", passage) for passage in passages
            ):
                problems.append(
                    f"The cited excerpts do not define '{value} {measure}'. Remove this numerical claim; do not invent what a percentage measures."
                )
    if mode == "ship30":
        count = word_count(content)
        if count < 1125:
            problems.append(
                f"The draft has {count} words. Add about {1250 - count} words of clearly labelled practical application and evidence-supported explanation. Preserve the existing useful material rather than summarizing it. Return the complete 1125–1375-word essay."
            )
        elif count > 1375:
            problems.append(
                f"The draft has {count} words. Remove about {count - 1250} words of repetition while preserving the evidence and conclusion. Return the complete 1125–1375-word essay."
            )
        if len(re.findall(r"^## ", content, re.M)) < 3 or not re.search(r"^# ", content, re.M):
            problems.append(
                "Start with '# Title' and use at least three '## Section heading' lines. Bold text is not a Markdown heading."
            )
        if "**" not in content or not re.search(r"^[-*] ", content, re.M):
            problems.append("Include selective bold emphasis and a useful bullet list.")
    elif mode == "html" and not re.search(r"<(?:html|main|section|div|article)\b", content, re.I):
        problems.append("Return complete HTML with embedded CSS, without code fences.")
    elif mode == "markdown" and not re.search(r"^# ", content, re.M):
        problems.append("Return a Markdown document with a title.")
    return problems


def requested_constraints(content: str, request: str) -> list[str]:
    """Validate explicit small output limits independently of source validation."""
    html = BeautifulSoup(content, "html.parser") if re.search(r"<(?:html|main|article)\b", content, re.I) else None
    numbers = {word: index for index, word in enumerate(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"],
    )}

    def number(value: str) -> int:
        return int(value) if value.isdigit() else numbers.get(value.lower(), 0)

    problems = []
    count = re.search(
        r"\bexactly\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:(?:actionable|numbered)\s+)?(checklist items?|tips?|items?|bullets?)\b", request, re.I,
    )
    if count:
        pattern = r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?" if count[2].lower().startswith("checklist") else (
            r"^\s*\d+[.)]\s+" if re.search(r"\bnumbered\b", request, re.I) else r"^\s*(?:\d+[.)]|[-*])\s+"
        )
        actual = len(html.select("ol > li" if re.search(r"\bnumbered\b", request, re.I) else "li")) if html else len(
            re.findall(pattern, content, re.M)
        )
        if actual != number(count[1]):
            problems.append(f"Return exactly {number(count[1])} {count[2]}; preserve the requested list format.")
    limit = re.search(
        r"\b(?:at most\s+)?(\d+|one|two|three|single)\s+(?:short\s+)?sentences?\b", request, re.I,
    )
    if limit:
        maximum = 1 if limit[1].lower() == "single" else number(limit[1])
        prose = re.sub(r"\[S\d+\]", "", html.get_text(" ", strip=True) if html else content).strip()
        sentences = len(re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", prose)) if prose else 0
        if sentences > maximum:
            problems.append(f"Use no more than {maximum} sentences.")
    title = re.search(r'\btitled\s+["“]([^"”]+)["”]', request, re.I)
    if title:
        if html:
            heading = html.find("h1")
            matches = heading is not None and heading.get_text(" ", strip=True) == title[1]
        else:
            matches = bool(re.search(rf"^#\s+{re.escape(title[1])}\s*$", content, re.M))
        if not matches:
            problems.append(f"Use the requested title as the document heading: {title[1]}")
    return problems


def unprovided_numbers(content: str, request: str, history: list[dict]) -> list[str]:
    """Reject invented numeric details in original writing that reuses user context."""
    number_words = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
        "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    }

    def values(text: str) -> set[str]:
        if re.search(r"<(?:html|main|article|section|div)\b", text, re.I):
            document = BeautifulSoup(text, "html.parser")
            for node in document.find_all(["style", "script", "head"]):
                node.decompose()
            text = document.get_text(" ", strip=True)
        text = re.sub(r"\[S\d+\]", " ", text.lower())
        found = {str(float(value.replace(",", ""))).rstrip("0").rstrip(".") for value in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", text)}
        found.update(str(number_words[word]) for word in re.findall(r"\b(?:" + "|".join(number_words) + r")\b", text))
        return found

    supplied = values(request)
    supplied.update(values(" ".join(item.get("content", "") for item in history if item.get("role") == "user")))
    invented = values(content) - supplied
    return [
        "Remove numeric details that do not appear in the request or user-provided conversation history: "
        + ", ".join(sorted(invented, key=lambda item: (float(item) if item.replace(".", "", 1).isdigit() else float("inf"), item)))
    ] if invented else []
