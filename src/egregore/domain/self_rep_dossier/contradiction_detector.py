"""Detect contradictions and corroborations between claims.

Conservative and scalable. Uses an inverted index over content words but only
compares claims from different party roles with date overlap and explicit
polarity conflict.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from egregore.domain.self_rep_dossier.dossier_models import Contradiction, Corroboration

if TYPE_CHECKING:
    from egregore.domain.self_rep_dossier.dossier_models import Claim


# Pairs of opposite concepts (lowercase, canonical forms).
POLARITY_PAIRS: tuple[tuple[set[str], set[str]], ...] = (
    (
        {"fit", "apt", "capable", "able", "fonctionnel"},
        {"unfit", "incapable", "unable", "inapte"},
    ),
    (
        {"approved", "accepted", "granted", "accorde", "accepte", "approuve"},
        {"denied", "refused", "rejected", "refuse", "rejete"},
    ),
    ({"present", "work", "travail", "return"}, {"absent", "absence", "missing"}),
    ({"safe", "securitaire"}, {"unsafe", "dangereux", "dangerous"}),
    ({"modified", "adapte", "light", "allegees"}, {"regular", "full", "regulieres"}),
    ({"can", "may", "peut", "could"}, {"cannot", "cant", "ne", "pas"}),
)

_STOP_WORDS = {
    "the",
    "le",
    "la",
    "les",
    "a",
    "un",
    "une",
    "de",
    "du",
    "des",
    "et",
    "and",
    "ce",
    "cette",
    "se",
    "subject",
    "document",
    "titled",
    "re",
    "fwd",
    "pour",
    "dans",
    "en",
    "sur",
    "avec",
    "est",
    "are",
    "is",
    "was",
    "were",
    "to",
    "jpg",
    "jpeg",
    "png",
    "pdf",
    "docx",
    "mp4",
    "m4a",
    "eml",
    "that",
    "this",
    "these",
    "those",
    "of",
    "for",
    "on",
    "as",
    "by",
    "or",
    "be",
    "been",
    "case",
    "party",
    "file",
    "dossier",
    "request",
    "claim",
    "plaintiff",
    "defendant",
    "demandeur",
    "defendeur",
    "appelant",
    "intime",
    "partie",
    "parties",
    "que",
    "qui",
    "dont",
    "ou",
    "son",
    "sa",
    "ses",
    "notre",
    "votre",
    "leur",
    "mon",
    "ton",
    "ma",
    "ta",
    "mes",
    "tes",
    "y",
    "il",
    "elle",
    "nous",
    "vous",
    "ils",
    "elles",
    "lui",
    "moi",
    "toi",
    "veuillez",
    "prier",
    "agreer",
    "salutations",
    "meilleures",
}


def _party_role(claim: Claim) -> str:
    """Return the effective party role, inferring from actor_id if unset."""
    if claim.party_role:
        return claim.party_role
    return claim.actor_id.split(":")[-1]


def _normalize(text: str) -> set[str]:
    """Return a set of normalized content words."""
    text = text.lower()
    text = re.sub(r"[àâä]", "a", text)
    text = re.sub(r"[éèêë]", "e", text)
    text = re.sub(r"[îï]", "i", text)
    text = re.sub(r"[ôö]", "o", text)
    text = re.sub(r"[ùûü]", "u", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = set()
    for w in text.split():
        w = w.strip()
        if len(w) > 2 and w not in _STOP_WORDS:
            words.add(w)
    return words


def _extract_dates(text: str) -> set[str]:
    """Extract ISO-like date substrings from claim text."""
    return set(re.findall(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", text))


def _polarity_conflict(words_a: set[str], words_b: set[str]) -> str | None:
    """Return reason if claims express opposite polarity on the same concept."""
    for pos_set, neg_set in POLARITY_PAIRS:
        a_pos = bool(words_a & pos_set)
        a_neg = bool(words_a & neg_set)
        b_pos = bool(words_b & pos_set)
        b_neg = bool(words_b & neg_set)

        if (a_pos and b_neg) or (a_neg and b_pos):
            concept = next(iter(pos_set))
            return f"opposite positions on '{concept}'"
    return None


def detect_contradictions_and_corroborations(  # noqa: C901
    claims: Iterable[Claim],
) -> tuple[tuple[Contradiction, ...], tuple[Corroboration, ...]]:
    """Scan claims for contradictions and corroborations.

    Restrictions:
    - Only compares claims from different actors.
    - Only compares claims from different party roles (e.g. employer vs union).
    - Requires date overlap for a contradiction.
    - Requires explicit polarity conflict.
    """
    claim_list = list(claims)

    # Precompute normalized words and dates.
    claim_words: list[set[str]] = []
    claim_dates: list[set[str]] = []
    for c in claim_list:
        claim_words.append(_normalize(c.text))
        claim_dates.append(_extract_dates(c.text))

    # Build inverted index: word -> claim indices.
    index: dict[str, set[int]] = defaultdict(set)
    for i, words in enumerate(claim_words):
        if not words:
            continue
        for w in words:
            index[w].add(i)

    contradictions: list[Contradiction] = []
    corroborations: list[Corroboration] = []
    seen_pairs: set[tuple[str, str]] = set()

    for i, c1 in enumerate(claim_list):
        words_a = claim_words[i]
        if not words_a:
            continue
        if c1.actor_id == "system:anchorum":
            continue

        # Candidate pairs: claims sharing at least 2 content words.
        candidate_counts: dict[int, int] = defaultdict(int)
        for w in words_a:
            for j in index.get(w, ()):
                if j <= i:
                    continue
                candidate_counts[j] += 1

        for j, overlap_count in candidate_counts.items():
            if overlap_count < 2:
                continue

            c2 = claim_list[j]

            # Must be different actors and different party roles.
            if c1.actor_id == c2.actor_id:
                continue
            if c1.actor_id == "system:anchorum" or c2.actor_id == "system:anchorum":
                continue
            role_a = _party_role(c1)
            role_b = _party_role(c2)
            if role_a == role_b:
                continue
            opposing = {role_a, role_b} in (
                {"claimant", "employer"},
                {"claimant", "insurer"},
                {"claimant", "union"},
                {"employer", "union"},
                {"employer", "insurer"},
                {"insurer", "medical"},
            )

            pair = tuple(sorted([c1.claim_id, c2.claim_id]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            words_b = claim_words[j]
            shared = words_a & words_b
            if len(shared) < 2:
                continue

            # Date overlap required for contradictions.
            dates1 = claim_dates[i]
            dates2 = claim_dates[j]
            date_overlap = bool(dates1 and dates2 and (dates1 & dates2))

            conflict_reason = _polarity_conflict(words_a, words_b)
            if conflict_reason and (date_overlap or opposing):
                contradictions.append(
                    Contradiction(
                        claim_a_id=c1.claim_id,
                        claim_b_id=c2.claim_id,
                        reason=conflict_reason,
                        normalized_subject=" / ".join(sorted(shared))[:200],
                        confidence=0.85 if date_overlap else 0.70,
                    )
                )
                continue

            # Corroboration: same claim type, independent actors, shared subject, date overlap.
            if (
                c1.claim_type == c2.claim_type
                and date_overlap
                and role_a != role_b
                and c1.claim_type in {"assertion", "admission"}
            ):
                corroborations.append(
                    Corroboration(
                        claim_a_id=c1.claim_id,
                        claim_b_id=c2.claim_id,
                        reason=f"independent {c1.claim_type} on the same date/subject",
                        normalized_subject=" / ".join(sorted(shared))[:200],
                        confidence=0.75,
                    )
                )

    return tuple(contradictions), tuple(corroborations)
