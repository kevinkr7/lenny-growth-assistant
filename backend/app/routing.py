"""Request intent and output constraints, independent of knowledge retrieval."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnPlan:
    intent: str
    output: str = "answer"
    search: bool = False
    exact: str | None = None
    one_sentence: bool = False
    references: tuple[str, ...] = ()


def exact_output(message: str) -> str | None:
    match = re.search(
        r"\b(?:reply|respond|return|output|say|print)(?:\s+with)?\s+exactly"
        r"(?:\s+(?:this|the following)(?:\s+(?:text|characters|string))?)?"
        r"(?:\s+and\s+nothing\s+else)?(?:,\s*(?:with|without)\b[^:\n]*)?\s*:\s*(.+)",
        message,
        re.I | re.S,
    )
    if match and not re.search(r"\b(?:if|unless)\b", message[:match.start()], re.I):
        value = match[1].strip()
        quoted = re.match(r'`([^`]+)`|"([^"]+)"|“([^”]+)”', value, re.S)
        if quoted:
            return next(group for group in quoted.groups() if group is not None)
        return re.split(r"\n|\s+(?:Do not|Don't|Without)\b", value, maxsplit=1, flags=re.I)[0].strip()

    # Also support the common smoke-test form without a colon, while requiring
    # a single literal token or quoted literal. This avoids treating
    # "Return exactly three tips: ..." as an exact literal.
    bare = re.search(
        r"\b(?:reply|respond|return|output|say|print)(?:\s+with)?\s+exactly\s+"
        r"(`([^`]+)`|\"([^\"]+)\"|“([^”]+)”|([A-Za-z0-9][A-Za-z0-9_-]{0,80}))"
        r"(?:\s*[.!?]\s*(?:and\s+nothing\s+else|do\s+not\b|don't\b|without\b).*)?\s*$",
        message,
        re.I | re.S,
    )
    if not bare or re.search(r"\b(?:if|unless)\b", message[:bare.start()], re.I):
        return None
    return next(group for group in bare.groups()[1:] if group is not None)

def _only_literal(message: str) -> str | None:
    match = re.search(
        r"\b(?:reply|respond|answer)\s+only\s+(`([^`]+)`|\"([^\"]+)\"|“([^”]+)”|([A-Za-z0-9][A-Za-z0-9_-]{0,80}))\s*[.!?]?\s*$",
        message,
        re.I,
    )
    if not match:
        return None
    return next(group for group in match.groups()[1:] if group is not None)


def abstention_text(message: str) -> str | None:
    """A user-specified absence response, not an unconditional exact-output task."""
    match = re.search(
        r"\bif\b[^.!?\n]{0,400}?\b(?:say|return|respond with|output)\s+"
        r'["“`]([^"”`]+)["”`]',
        message,
        re.I,
    )
    if match and re.search(
        r"\b(?:no (?:relevant|supporting|available) (?:passage|evidence|information)|"
        r"(?:do|does) not (?:provide|contain|include)|information is (?:unavailable|not available)|"
        r"(?:cannot|can't|couldn't) (?:answer|find|verify))\b",
        match[1],
        re.I,
    ):
        return match[1]
    return None


def conversation_recall(message: str) -> bool:
    """Recognize an explicit request to recall this session's prior exchange.

    These requests must stay on the conversation path even when they mention a
    podcast topic.  The caller supplies the session-scoped history; no library
    search is needed to answer what the assistant previously concluded.
    """
    shared_outcome_reference = bool(
        re.search(
            r"\bwhat\s+(?:conclusion\s+did\s+we\s+reach|did\s+we\s+(?:conclude|decide|reach))\b",
            message,
            re.I,
        )
    )
    explicit_recall = bool(
        re.search(
            r"\bwhat\s+(?:exact\s+)?(?:test\s+)?(?:token|phrase|code|answer|detail)\s+did\s+i\s+ask\s+you\s+to\s+remember\b"
            r"|\bwhat\s+did\s+i\s+ask\s+you\s+to\s+remember\b"
            r"|\b(?:my|our|the)\s+other\s+(?:conversation|chat|session)\b",
            message,
            re.I,
        )
    )
    has_history_reference = bool(
        re.search(
            r"\b(?:conversation|chat|session)\s+history\b"
            r"|\b(?:earlier|previously|before|last\s+time)\b[^.!?\n]{0,240}"
            r"\b(?:asked|ask|answer(?:ed)?|conclu(?:de|sion)|question)\b"
            r"|\bwhat\s+(?:did\s+you\s+(?:tell\s+me|say|answer|conclude)|"
            r"did\s+we\s+(?:conclude|decide|reach)|"
            r"conclusion\s+did\s+(?:you|we)\s+reach|was\s+your\s+conclusion)\b",
            message,
            re.I | re.S,
        )
    )
    if shared_outcome_reference or explicit_recall:
        return True
    if not has_history_reference:
        return False
    return bool(
        re.search(
            r"\b(?:based\s+on|from|using)\s+(?:our|the|this)\s+(?:conversation|chat|session)"
            r"(?:\s+history)?\b"
            r"|\b(?:not|without|instead\s+of|rather\s+than)\b[^.!?\n]{0,100}"
            r"\b(?:podcast|transcript|source|retriev\w*|library|knowledge\s+base)\w*",
            message,
            re.I | re.S,
        )
    )


def references_prior_context(message: str) -> bool:
    """Whether the user explicitly asks to reuse details or material in history."""
    return bool(
        re.search(
            r"\b(?:details?|information|product|budget|token|answer|conclusion|excerpts?|ideas?)\b"
            r"[^.!?\n]{0,140}\b(?:i\s+(?:gave|shared|provided|told)|we\s+(?:discussed|covered|agreed)|"
            r"just\s+discussed|from\s+earlier)\b"
            r"|\b(?:based\s+on|using|from)\s+(?:our|the|this)\s+(?:conversation|chat|session)\b",
            message,
            re.I | re.S,
        )
    )


def remember_instruction(message: str) -> bool:
    return bool(re.search(r"\b(?:remember|keep in mind|save)\s+(?:this|that|the following|my)\b", message, re.I))


def context_declaration(message: str) -> bool:
    return bool(
        re.search(r"\bfor\s+this\s+(?:test|experiment|conversation)\b", message, re.I)
        and re.search(r"\b(product|budget|audience|context|goal)\b", message, re.I)
    )


def plan_turn(message: str, mode: str = "auto") -> TurnPlan:
    no_search = bool(
        re.search(
            r"\b(?:do not|don't|without|never|no)\s+(?:(?:using|use|the|any)\s+)*(?:search\w*|retriev\w*|brows\w*|knowledge\s+base|look\w*\s+up)",
            message,
            re.I,
        )
    )
    exact = exact_output(message)
    if exact is None:
        exact = _only_literal(message)
    single = bool(re.search(r"\b(?:one|1|single)\s+sentence\b", message, re.I))
    references = tuple(dict.fromkeys(ref.upper() for ref in re.findall(r"\bS\d+\b", message, re.I)))
    if exact is not None:
        return TurnPlan("conversation", exact=exact, one_sentence=single)
    history_reference = references_prior_context(message)
    memory_write = remember_instruction(message)
    # Negative output instructions must not select the format they prohibit.
    positive = re.sub(r"\b(?:do not|don't|never)\b[^.;\n]*(?:[.;]|$)", "", message, flags=re.I)
    output = mode if mode != "auto" else "answer"
    if mode == "auto" and re.search(r"\bmarkdown\b", positive, re.I) and re.search(r"\bhtml\b", positive, re.I):
        output = "both"
    elif mode == "auto" and re.search(
        r"\b(ship\s*30|essay|long.form|(?:1,?250|twelve hundred)\s.words?)\b", positive, re.I
    ):
        output = "ship30"
    elif mode == "auto" and re.search(r"\b(html|landing page|web ?page|css)\b", positive, re.I):
        output = "html"
    elif (
        mode == "auto"
        and re.search(r"\b(markdown|document|one.pager|brief|playbook)\b", positive, re.I)
        and re.search(r"\b(create|write|turn|make|generate|draft)\b", positive, re.I)
    ):
        output = "markdown"
    if output in {"ship30", "markdown", "html", "both"}:
        original_template = bool(re.search(r"\b(?:checklist|planner|template)\b", positive, re.I))
        explicitly_grounded = bool(re.search(
            r"\b(?:according\s+to|grounded\s+in|based\s+on\s+(?:the\s+)?(?:retrieved|provided|podcast|transcript|excerpt|source)|"
            r"from\s+(?:Lenny|the\s+podcast|the\s+transcript)|cite\s+(?:the\s+)?sources?)\b",
            positive,
            re.I,
        ))
        search = not no_search and not references and not history_reference and not (
            original_template and not explicitly_grounded
        )
        return TurnPlan("writing", output, search, one_sentence=single, references=references)
    if conversation_recall(message) or memory_write or context_declaration(message):
        return TurnPlan("conversation", one_sentence=single)
    library = bool(re.search(r"\b(indexed|library|knowledge base|transcript|episode|podcast|source|metadata)\b", positive, re.I))
    extraction = bool(re.search(r"\b(give|return|show|list|provide|extract|report|what is|what are|who is|when)\b", positive, re.I))
    metadata = bool(re.search(
        r"\b(title|author|publication date|metadata|provenance|guest name|transcript url)\b", positive, re.I,
    )) and bool(references or (library and extraction))
    if references and re.search(r"\b(date|speaker)\b", positive, re.I):
        metadata = True
    quote = bool(re.search(r"\b(quote|quotation|verbatim|exact (?:supporting )?passage)\b", positive, re.I))
    raw = bool(re.search(r"\b(?:only (?:the )?(?:retrieved |raw )?evidence|raw evidence)\b", positive, re.I))
    if references or (not no_search and (metadata or (library and quote) or raw)):
        intent = (
            "evidence" if raw else "quote_metadata" if metadata and quote else "metadata"
            if metadata else "quote" if quote else "followup"
        )
        return TurnPlan(intent, search=not references, references=references, one_sentence=single)
    # Task classes, not specific keywords/literals from a smoke test.
    conversational = bool(
        re.search(
            r"^\s*(?:hi|hello|hey|thanks|thank you|good morning|good evening)\b|\b(?:greet|greeting|acknowledge|repeat after me|what can you (?:do|help)|explain what you can help)\b|^\s*(?:please\s+)?(?:rewrite|reformat|translate|capitalize|lowercase|uppercase|summarize this text|format (?:this|the following))\b",
            message,
            re.I,
        )
    )
    factual = bool(
        re.search(
            r"\b(?:what|how|why|which|explain|tell me)\b.*\b(?:product|growth|retention|pricing|Lenny|podcast|interview|framework)\b",
            message,
            re.I,
        )
    )
    if no_search or (conversational and not factual):
        return TurnPlan("conversation", one_sentence=single)
    return TurnPlan("knowledge", search=True, one_sentence=single)
