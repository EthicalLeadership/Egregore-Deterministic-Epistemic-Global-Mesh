"""
domain/agency_taxonomy.py

Agency Taxonomy — Crust Layer / Species Classification.

The crust is where all species live, evolve, and die. This module defines
the formal taxonomy of agencies, biomes, and lobes that populate the crust.

Species:
- ACADEMIC: produces theory, models, formal proofs
- DEFENSIVE: maintains equilibrium, guards boundaries
- INTELLIGENCE: tracks threats, reconnaissance, surveillance
- PRODUCTIVE: executes work, generates value
- USELESS: exists without productive function (aesthetic, philosophical, experimental)

Biomes: environments where species operate
Lobes: functional divisions within a species
Lifecycle: live → die → sediment (fossil register)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class Species(Enum):
    """All species that populate the crust."""

    ACADEMIC = auto()
    DEFENSIVE = auto()
    INTELLIGENCE = auto()
    PRODUCTIVE = auto()
    USELESS = auto()


class Biome(Enum):
    """Environments where species operate."""

    RESEARCH = auto()  # Academic species
    FORTRESS = auto()  # Defensive species
    WILDERNESS = auto()  # Intelligence species
    FACTORY = auto()  # Productive species
    GARDEN = auto()  # Useless species (aesthetic, experimental)


class Lobe(Enum):
    """Functional divisions within a species."""

    COGNITION = auto()
    MEMORY = auto()
    PERCEPTION = auto()
    ACTION = auto()
    METABOLISM = auto()


@dataclass(frozen=True)
class AgencyId:
    """Unique identifier for an agency (species instance)."""

    species: Species
    biome: Biome
    lobe: Lobe
    instance_tag: str

    @property
    def raw(self) -> str:
        return f"{self.species.name}:{self.biome.name}:{self.lobe.name}:{self.instance_tag}"


@dataclass
class AgencyState:
    """Current state of a living agency."""

    agency_id: AgencyId
    alive: bool = True
    energy_consumed_j: float = 0.0
    work_units_processed: int = 0
    work_units_quarantined: int = 0
    birth_timestamp_ns: int = 0
    death_timestamp_ns: int | None = None
    sediment_id: str | None = None  # Set when agency dies and becomes fossil


@dataclass
class CrustPopulation:
    """Registry of all living agencies on the crust."""

    agencies: dict[str, AgencyState] = field(default_factory=dict)
    _species_count: dict[Species, int] = field(default_factory=dict)

    def register(self, agency: AgencyState) -> None:
        self.agencies[agency.agency_id.raw] = agency
        self._species_count[agency.agency_id.species] = (
            self._species_count.get(agency.agency_id.species, 0) + 1
        )

    def kill(self, agency_id: AgencyId, sediment_id: str, timestamp_ns: int) -> None:
        if agency_id.raw in self.agencies:
            agency = self.agencies[agency_id.raw]
            agency.alive = False
            agency.death_timestamp_ns = timestamp_ns
            agency.sediment_id = sediment_id
            self._species_count[agency_id.species] = max(
                0, self._species_count.get(agency_id.species, 0) - 1
            )

    def living_by_species(self, species: Species) -> list[AgencyState]:
        return [
            a
            for a in self.agencies.values()
            if a.alive and a.agency_id.species == species
        ]

    def fossilize_all_dead(self, archive) -> list[str]:
        """Move all dead agencies to sediment archive. Returns list of sediment IDs."""
        sediment_ids = []
        for agency in list(self.agencies.values()):
            if not agency.alive and agency.sediment_id is not None:
                archive.fossilize(agency)
                sediment_ids.append(agency.sediment_id)
        return sediment_ids
