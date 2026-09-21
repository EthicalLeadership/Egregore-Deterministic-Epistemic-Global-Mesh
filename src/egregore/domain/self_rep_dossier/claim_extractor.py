"""Extract claims/propositions from artifacts deterministically.

No LLM is required. The extractor uses pattern matching over available text:
email subjects, filenames, text file bodies, and anomaly descriptions.
"""

from __future__ import annotations

import re
import uuid

from egregore.domain.self_rep_dossier.dossier_models import Artifact, Claim

# Patterns that suggest a specific claim type.
REFUSAL_PATTERNS = (
    r"\brefus\w*\b",
    r"\bdenied\b",
    r"\bdenies\b",
    r"\breject\w*\b",
    r"\bne peut pas\b",
    r"\bne pas \w+ (?:accorder|approuver|accepter)\b",
    r"\bwill not\b",
    r"\bcannot\b",
    r"\bnot approved\b",
    r"\bnot accepted\b",
)

OBLIGATION_PATTERNS = (
    r"\bdoit\b",
    r"\bdoivent\b",
    r"\bmust\b",
    r"\bshall\b",
    r"\boblig\w*\b",
    r"\brequis\b",
    r"\brequired\b",
    r"\bresponsabilit\w*\b",
    r"\bduty\b",
)

REQUEST_PATTERNS = (
    r"\bdemande\b",
    r"\bdemand\w*\b",
    r"\brequest\w*\b",
    r"\basks?\b",
    r"\bseek\w*\b",
    r"\bsollicit\w*\b",
)

ADMISSION_PATTERNS = (
    r"\badmet\b",
    r"\badmits\b",
    r"\bconfirme\b",
    r"\bconfirms\b",
    r"\baccord\b",
    r"\bagree\w*\b",
    r"\baccept\w*\b",
)

DATE_PATTERN = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
    r"\d{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4})\b",
    re.I,
)

SENTENCE_SPLITTER = re.compile(r"(?<=[.!?])\s+(?=[A-ZÉÀÈÙÂÊÎÔÛÄËÏÖÜ])")

# Phrases that indicate boilerplate/noise.
NOISE_PATTERNS = (
    r"^\s*cordialement",
    r"^\s*best regards",
    r"^\s*regards",
    r"^\s*sincerely",
    r"^\s*merci",
    r"^\s*thank you",
    r"^\s*confidential",
    r"^\s*this\s+(?:message|email|document|is)\s+(?:is\s+)?(?:confidential|privileged|private)\b",
    r"^\s*privilégié",
    r"^\s*denégation",
    r"^\s*disclaimer",
    r"^\s*if you received this",
    r"^\s*si vous avez reçu",
    r"^\s*si cet e-mail ne vous est pas destiné",
    r"^\s*lisez votre courriel sécurisé",
    r"^\s*qu'est-ce qu'un courriel",
    r"^\s*votre message sera disponible",
    r"^\s*party:\s*\d",
    r"^\s*case:\s*\w",
    r"^\s*\d{1,2}:\d{2}\s*[ap]m",
    r"^\s*to:\s*",
    r"^\s*from:\s*",
    r"^\s*cc:\s*",
    r"^\s*subject:\s*",
    r"^\s*sent:\s*",
    r"^\s*https?://",
    r"^\s*www\.",
    r"^\s*$",
    r"^\s*page\s+\d+\s+of\s+\d+",
    r"^\s*\d+\s*/\s*\d+\s*$",
)

MAX_CLAIMS_PER_ARTIFACT = 30


def _is_noise(text: str) -> bool:
    """Return True if text is boilerplate, signature, or OCR garbage."""
    lower = text.lower().strip()
    if len(lower) < 15:
        return True
    # Too many non-letter characters indicates OCR garbage.
    alpha_ratio = sum(1 for c in lower if c.isalpha()) / max(len(lower), 1)
    if alpha_ratio < 0.5 and len(lower) > 30:
        return True
    return any(re.search(pattern, lower, re.I) for pattern in NOISE_PATTERNS)


def _detect_claim_type(text: str) -> str:
    """Classify a sentence by speech-act type."""
    lower = text.lower()
    if any(re.search(p, lower) for p in REFUSAL_PATTERNS):
        return "refusal"
    if any(re.search(p, lower) for p in OBLIGATION_PATTERNS):
        return "obligation"
    if any(re.search(p, lower) for p in REQUEST_PATTERNS):
        return "request"
    if any(re.search(p, lower) for p in ADMISSION_PATTERNS):
        return "admission"
    return "assertion"


def _extract_date_mentions(text: str) -> list[str]:
    return [m.group(0) for m in DATE_PATTERN.finditer(text)]


def _sentence_value(text: str) -> int:
    """Score a sentence by apparent information density."""
    # Prefer sentences with dates, named entities (capitalized words), and verbs.
    score = 0
    if DATE_PATTERN.search(text):
        score += 3
    # Count capitalized words as potential named entities.
    score += len(re.findall(r"\b[A-ZÉÀÈÙÂÊÎÔÛÄËÏÖÜ][a-zéàèùâêîôûäëïöü]{2,}\b", text))
    # Penalize very short and very long sentences.
    length = len(text)
    if length < 30:
        score -= 5
    if length > 400:
        score -= 3
    # Penalize sentences that are mostly numbers/symbols.
    alpha_ratio = sum(1 for c in text if c.isalpha()) / max(length, 1)
    if alpha_ratio < 0.6:
        score -= 5
    return score


def _claim_from_text(
    text: str,
    artifact: Artifact,
    actor_id: str,
    party_role: str,
    prefix: str = "",
) -> Claim | None:
    """Create a single claim from a text fragment."""
    text = text.strip()
    if not text or len(text) < 15 or _is_noise(text):
        return None

    claim_type = _detect_claim_type(text)
    dates = _extract_date_mentions(text)
    if dates:
        text = f"{text} [dates: {', '.join(dates)}]"

    return Claim(
        claim_id=f"claim:{artifact.artifact_id[:16]}:{uuid.uuid4().hex[:8]}",
        text=f"{prefix}{text}",
        source_artifact_ids=(artifact.artifact_id,),
        actor_id=actor_id,
        timestamp=artifact.timestamp,
        modality=artifact.modality,
        claim_type=claim_type,
        party_role=party_role,
        confidence=0.9 if artifact.modality == "email" else 0.75,
        extracted_by="deterministic",
        supporting_evidence_ids=(),
    )


def _sentences(text: str) -> list[str]:
    """Split text into sentences, filtering out very short fragments."""
    if not text:
        return []
    parts = SENTENCE_SPLITTER.split(text)
    return [p.strip() for p in parts if len(p.strip()) >= 15]


def extract_claims_from_artifact(  # noqa: C901
    artifact: Artifact,
    actor_id: str,
    party_role: str = "",
) -> list[Claim]:
    """Extract claims from a single artifact."""
    claims: list[Claim] = []

    # 1. Email subject is high-value.
    subject = artifact.metadata.get("subject")
    if subject and str(subject).strip():
        claim = _claim_from_text(
            str(subject).strip(), artifact, actor_id, party_role, prefix="Subject: "
        )
        if claim:
            claims.append(claim)

    # 2. Body text, if available. Limit to the most substantive sentences.
    if artifact.content_text:
        candidate_sentences = _sentences(artifact.content_text)
        # Score and keep top N substantive sentences.
        scored = sorted(
            ((s, _sentence_value(s)) for s in candidate_sentences),
            key=lambda x: x[1],
            reverse=True,
        )
        kept = 0
        for sentence, _score in scored:
            if kept >= MAX_CLAIMS_PER_ARTIFACT:
                break
            claim = _claim_from_text(sentence, artifact, actor_id, party_role)
            if claim:
                claims.append(claim)
                kept += 1

    # 3. Filename as a claim source when no body exists and filename is informative.
    if (
        not artifact.content_text
        and artifact.filename
        and artifact.modality in ("document", "text", "data", "email")
    ):
        meaningful = any(
            word.lower()
            not in (
                "jpg",
                "jpeg",
                "png",
                "pdf",
                "docx",
                "mp4",
                "m4a",
                "csv",
                "json",
                "txt",
            )
            and len(word) > 4
            for word in artifact.filename.replace("_", " ").replace("-", " ").split()
        )
        if meaningful:
            claim = _claim_from_text(
                f"Document titled '{artifact.filename}'", artifact, actor_id, party_role
            )
            if claim:
                claims.append(claim)

    # 4. Anomaly descriptions become system observations.
    for anomaly in artifact.anomalies:
        claims.append(
            Claim(
                claim_id=f"claim:{artifact.artifact_id[:16]}:anom:{uuid.uuid4().hex[:8]}",
                text=f"[ANCHORUM finding] {anomaly}",
                source_artifact_ids=(artifact.artifact_id,),
                actor_id="system:anchorum",
                timestamp=artifact.timestamp,
                modality="system",
                claim_type="assertion",
                party_role="system",
                confidence=0.85,
                extracted_by="deterministic",
                supporting_evidence_ids=(),
            )
        )

    return claims
