"""Classify artifacts and email addresses into actor/party roles.

This module is intentionally deterministic: it uses domain rules, known
addresses, and filename heuristics. No LLM is involved in actor classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from egregore.domain.self_rep_dossier.dossier_models import Actor, Artifact

# Known institutional domains and their default roles.
DOMAIN_ROLES: dict[str, tuple[str, str, str]] = {
    "example.com": ("employer", "Acme Corp", "Employer / management"),
    "insurer.example.com": (
        "insurer",
        "Example Insurer",
        "Disability insurer / case manager",
    ),
    "union.example.com": ("union", "Example Union", "Union representative"),
    "clinic.example.com": (
        "medical",
        "Example Clinic",
        "Medical clinic / appointment system",
    ),
}

# Known individual addresses with explicit labels.
KNOWN_EMAILS: dict[str, tuple[str, str, str]] = {
    "claimant@example.net": ("claimant", "Claimant", "Self-represented claimant"),
    "claimant@example.org": ("claimant", "Claimant", "Self-represented claimant"),
    "claimant@example.com": ("claimant", "Claimant", "Self-represented claimant"),
    "hr@example.com": ("employer", "HR Operations (Acme)", "Employer HR"),
    "alice.smith@example.com": (
        "employer",
        "Alice Smith (Acme)",
        "Employer representative",
    ),
    "bob.jones@example.com": (
        "employer",
        "Bob Jones (Acme)",
        "Employer representative",
    ),
    "occupational.health@example.com": (
        "employer",
        "Occupational Health (Acme)",
        "Employer medical/occupational health",
    ),
    "case.manager@insurer.example.com": (
        "insurer",
        "Case Manager (Example Insurer)",
        "Insurer representative",
    ),
    "claims@insurer.example.com": (
        "insurer",
        "Claims (Example Insurer)",
        "Insurer representative",
    ),
    "rep@union.example.com": (
        "union",
        "Union Rep (Example Union)",
        "Union representative",
    ),
    "steward@union.example.com": (
        "union",
        "Union Steward (Example Union)",
        "Union representative",
    ),
    "info@clinic.example.com": ("medical", "Example Clinic Info", "Medical clinic"),
    "appointments@clinic.example.com": (
        "medical",
        "Appointments (Example Clinic)",
        "Medical clinic",
    ),
}

# Email domains that belong to the claimant personally.
CLAIMANT_DOMAINS = {
    "example.net",
    "example.org",
    "example.com",
    "hotmail.com",
    "hotmail.fr",
    "protonmail.com",
    "gmail.com",
    "outlook.com",
}

# Filename heuristics for claimant-generated evidence.
CLAIMANT_FILENAME_PATTERNS: tuple[str, ...] = (
    "lettre",
    "demande",
    "plainte",
    "reclamation",
    "symptomatologie",
    "medical package",
    "dossier",
)

# Filename heuristics for institutional evidence.
EMPLOYER_FILENAME_PATTERNS: tuple[str, ...] = ("acme", "employer", "hr")
INSURER_FILENAME_PATTERNS: tuple[str, ...] = (
    "example_insurer",
    "insurance",
    "assurance",
    "absence",
)
UNION_FILENAME_PATTERNS: tuple[str, ...] = (
    "example_union",
    "syndicat",
    "union",
    "guide syndiqu",
)
MEDICAL_FILENAME_PATTERNS: tuple[str, ...] = (
    "medical",
    "medecin",
    "doctor",
    "example_clinic",
)


@dataclass
class ActorRegistry:
    """Builds and stores actors derived from artifacts."""

    actors: dict[str, Actor] = field(default_factory=dict)
    actor_by_email: dict[str, str] = field(default_factory=dict)
    actor_by_domain: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Seed canonical claimant first.
        self._claimant_actor_id()

        # Seed known emails; claimant emails all map to canonical claimant.
        for email, (role, name, stake) in KNOWN_EMAILS.items():
            if role == "claimant":
                self.actor_by_email[email.lower()] = self._claimant_actor_id()
                continue
            actor_id = self._actor_id_for_email(email)
            self.actors[actor_id] = Actor(
                actor_id=actor_id,
                display_name=name,
                party_role=role,
                email_addresses=(email,),
                domains=(),
                aliases=(),
                stake=stake,
                authority_level="primary" if role in ("employer",) else "secondary",
            )
            self.actor_by_email[email.lower()] = actor_id

        # Seed known domains.
        for domain, (role, name, stake) in DOMAIN_ROLES.items():
            actor_id = f"actor:{role}:{domain.replace('.', '_')}"
            self.actors[actor_id] = Actor(
                actor_id=actor_id,
                display_name=name,
                party_role=role,
                email_addresses=(),
                domains=(domain,),
                aliases=(),
                stake=stake,
                authority_level="secondary",
            )
            self.actor_by_domain[domain.lower()] = actor_id

    @staticmethod
    def _actor_id_for_email(email: str) -> str:
        local, _, domain = email.lower().partition("@")
        safe_local = "".join(c if c.isalnum() else "_" for c in local)
        safe_domain = domain.replace(".", "_")
        return f"actor:email:{safe_local}_{safe_domain}"

    def _claimant_actor_id(self) -> str:
        """Return the canonical claimant actor id, creating it if necessary."""
        actor_id = "actor:claimant:self_represented"
        if actor_id not in self.actors:
            self.actors[actor_id] = Actor(
                actor_id=actor_id,
                display_name="Claimant (self-represented)",
                party_role="claimant",
                email_addresses=tuple(
                    e for e, (role, _, _) in KNOWN_EMAILS.items() if role == "claimant"
                ),
                domains=tuple(CLAIMANT_DOMAINS),
                aliases=(),
                stake="Self-represented claimant seeking remedy",
                authority_level="primary",
            )
        return actor_id

    def _get_or_create_actor_for_email(self, email: str) -> str:
        email = email.lower().strip()
        if not email:
            return "actor:unknown"
        if email in self.actor_by_email:
            return self.actor_by_email[email]

        # Personal domains route to canonical claimant.
        domain = email.split("@")[-1] if "@" in email else ""
        if domain.lower() in CLAIMANT_DOMAINS:
            actor_id = self._claimant_actor_id()
            self.actor_by_email[email] = actor_id
            return actor_id

        # Institutional domain fallback.
        if domain and domain.lower() in self.actor_by_domain:
            actor_id = self.actor_by_domain[domain.lower()]
            self.actor_by_email[email] = actor_id
            return actor_id

        # Unknown actor.
        actor_id = self._actor_id_for_email(email)
        self.actors[actor_id] = Actor(
            actor_id=actor_id,
            display_name=email,
            party_role="unknown",
            email_addresses=(email,),
            domains=(domain,) if domain else (),
            aliases=(),
            stake="Unknown party",
            authority_level="unknown",
        )
        self.actor_by_email[email] = actor_id
        return actor_id

    def classify_artifact(self, artifact: Artifact) -> str:
        """Return the most likely actor_id for an artifact."""
        # 1. Explicit email From header.
        from_addr = artifact.metadata.get("from_addr")
        if from_addr:
            return self._get_or_create_actor_for_email(from_addr)

        # 2. Filename heuristics.
        filename_lower = artifact.filename.lower()
        if any(p in filename_lower for p in CLAIMANT_FILENAME_PATTERNS):
            return self._claimant_actor_id()
        if any(p in filename_lower for p in EMPLOYER_FILENAME_PATTERNS):
            return self._ensure_party_actor("employer", "Acme Corp (unspecified)")
        if any(p in filename_lower for p in INSURER_FILENAME_PATTERNS):
            return self._ensure_party_actor("insurer", "Example Insurer (unspecified)")
        if any(p in filename_lower for p in UNION_FILENAME_PATTERNS):
            return self._ensure_party_actor("union", "Example Union (unspecified)")
        if any(p in filename_lower for p in MEDICAL_FILENAME_PATTERNS):
            return self._ensure_party_actor("medical", "Medical provider (unspecified)")

        # 3. Source path heuristics.
        path_lower = artifact.source_path.lower()
        if any(p in path_lower for p in CLAIMANT_FILENAME_PATTERNS):
            return self._claimant_actor_id()

        # 4. Default to claimant for files in the main dossier root that are images/videos.
        if artifact.modality in ("image", "recording"):
            return self._claimant_actor_id()

        return "actor:unknown"

    def _ensure_party_actor(self, role: str, display_name: str) -> str:
        actor_id = f"actor:{role}:unspecified"
        if actor_id not in self.actors:
            self.actors[actor_id] = Actor(
                actor_id=actor_id,
                display_name=display_name,
                party_role=role,
                email_addresses=(),
                domains=(),
                aliases=(),
                stake=f"{role.capitalize()} party",
                authority_level="secondary",
            )
        return actor_id

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        """Apply user-provided actor overrides."""
        self.overrides.update(overrides)
        for actor_id, data in overrides.items():
            if actor_id in self.actors:
                existing = self.actors[actor_id]
                self.actors[actor_id] = Actor(
                    actor_id=actor_id,
                    display_name=data.get("display_name", existing.display_name),
                    party_role=data.get("party_role", existing.party_role),
                    email_addresses=tuple(
                        data.get("email_addresses", existing.email_addresses)
                    ),
                    domains=tuple(data.get("domains", existing.domains)),
                    aliases=tuple(data.get("aliases", existing.aliases)),
                    stake=data.get("stake", existing.stake),
                    authority_level=data.get(
                        "authority_level", existing.authority_level
                    ),
                )

    def all_actors(self) -> tuple[Actor, ...]:
        return tuple(self.actors.values())
