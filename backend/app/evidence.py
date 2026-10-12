"""Deterministic evidence identity, source inspection and metadata integrity."""

import re
from collections import Counter

SPEAKER = re.compile(
    r"(?:^|(?<=[.!?])\s+|\n)([A-Z][\w.'’-]*(?:[ \t]+[A-Z][\w.'’-]*){0,4})[ \t]+\((\d{1,2}:\d{2}(?::\d{2})?)\):",
    re.M,
)
TIMESTAMP = re.compile(r"\((\d{1,2}:\d{2}(?::\d{2})?)\)")


def passage_metadata(text: str) -> dict:
    names = list(dict.fromkeys(match[0].strip() for match in SPEAKER.findall(text)))
    times = TIMESTAMP.findall(text)
    return {
        "speakers": names,
        "timestamp_start": times[0] if times else None,
        "timestamp_end": times[-1] if times else None,
    }


def contextual_passage_metadata(chunks: list[str], transcript: str) -> list[dict]:
    """Carry actual speaker labels over split turns, without changing excerpt text."""
    flat = " ".join(transcript.split())
    labels = []
    for match in SPEAKER.finditer(transcript):
        prefix = " ".join(transcript[: match.start(1)].split())
        labels.append((len(prefix) + bool(prefix), match[1].strip()))
    result, cursor = [], 0
    for chunk in chunks:
        start = flat.find(chunk, cursor)
        opening = None
        if start >= 0:
            opening = next((name for position, name in reversed(labels) if position <= start), None)
            cursor = start + 1  # Allow overlapping chunks, but advance past repeated text.
        metadata = passage_metadata(chunk)
        metadata["opening_speaker"] = opening
        metadata["speakers"] = list(dict.fromkeys(([opening] if opening else []) + metadata["speakers"]))
        result.append(metadata)
    return result


def metadata_integrity(metadata: dict, body: str) -> dict:
    speakers = Counter(name.strip() for name, _ in SPEAKER.findall(body))
    guests = [
        name for name, count in speakers.most_common() if count >= 2 and not re.match(r"^Lenny(?:\s|$)", name, re.I)
    ]
    declared = str(metadata.get("guest") or "").strip()
    title = str(metadata.get("title") or "")
    reasons = []
    if declared and guests and not any(declared.casefold() == name.casefold() for name in guests):
        reasons.append("The declared guest does not match the recurring transcript speakers.")
    # This archive names the interviewee after a title's final '|'. Avoid inferring names from prose.
    title_person = title.rsplit("|", 1)[-1].split("(", 1)[0].strip() if "|" in title else ""
    if (
        declared
        and re.fullmatch(r"[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3}", title_person)
        and title_person.casefold() != declared.casefold()
    ):
        reasons.append("The interviewee in the archive title conflicts with the declared guest.")
    return {
        "metadata_status": "conflicting" if reasons else "not_flagged",
        "metadata_warning": " ".join(reasons)
        + " Archive title/date/video remain unverified; original metadata and the immutable transcript are preserved."
        if reasons
        else None,
        "transcript_speakers": list(speakers),
        "observed_guests": guests,
        "original_metadata": metadata,
    }


def saved_evidence(history: list[dict]) -> list[dict]:
    registry = {}
    for message in history:
        meta = message.get("metadata", {})
        if meta.get("status") in {"failed", "cancelled"}:
            continue
        for evidence in meta.get("sources", []):
            # Legacy conversations reused labels per turn. The most recent displayed meaning wins.
            registry[evidence["citation"]] = evidence
    return list(registry.values())


def stable_citations(evidence: list[dict], registry: list[dict]) -> list[dict]:
    known = {(e["chunk_id"], e.get("revision")): e["citation"] for e in registry}
    next_id = max((int(e["citation"][1:]) for e in registry if re.fullmatch(r"S\d+", e["citation"])), default=0)
    result = []
    for source in evidence:
        key = (source["chunk_id"], source.get("revision"))
        citation = known.get(key)
        if citation is None:
            next_id += 1
            citation = f"S{next_id}"
        result.append({**source, "citation": citation})
    return result


def short_quote(excerpt: str, limit: int = 70) -> str:
    # Return a contiguous original substring, never a synthesized/paraphrased quotation.
    words = list(re.finditer(r"\S+", excerpt))
    return excerpt[: words[min(limit, len(words)) - 1].end()] if words else ""


def inspect_source(source: dict, metadata: bool = False, *, full_excerpt: bool = False) -> str:
    ref = source["citation"]
    title = source.get("episode_title") or "Unavailable"
    if not metadata:
        if not source.get("excerpt"):
            return f"The exact passage for [{ref}] is unavailable; I cannot quote it reliably."
        warning = f"Metadata warning: {source['metadata_warning']}\n\n" if source.get("metadata_warning") else ""
        excerpt = source["excerpt"]
        # Prefer the guest's actual explanation over an opening interviewer question.
        if not full_excerpt and source.get("opening_speaker") != source.get("guest"):
            for match in SPEAKER.finditer(excerpt):
                if match[1].casefold() == str(source.get("guest", "")).casefold():
                    excerpt = excerpt[match.start(1) :]
                    break
        return f"From **{title}** [{ref}]\n\n{warning}Verbatim passage:\n\n> {excerpt if full_excerpt else short_quote(excerpt)}"
    original = source.get("original_metadata") or {}
    date = source.get("publish_date") or "Unavailable or unverified"
    author = source.get("author") or original.get("author") or "Unavailable (the guest is not assumed to be the author)"
    text = f"**{ref} metadata**\n\n- Title: {title}\n- Author: {author}\n- Guest: {source.get('guest') or 'Unavailable'}\n- Publication date: {date}\n- Speakers in this passage: {', '.join(source.get('speakers') or []) or 'Unavailable'}\n- Timestamp: {source.get('timestamp_start') or 'Unavailable'}\n- Transcript provenance: {source.get('source_url') or 'Unavailable'}\n- Stable source ID: {source.get('source_id') or 'Unavailable'} [{ref}]"
    if source.get("metadata_warning"):
        text += f"\n\nMetadata warning: {source['metadata_warning']}"
    return text
