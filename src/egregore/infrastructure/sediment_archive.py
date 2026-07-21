"""
infrastructure/sediment_archive.py

Sediment Archive — Fossil Registers and Deep Memory.

When agencies die, they do not disappear. They become sediment — fossil
registers stratified by time. Future species can access these fossils as
memory, but fossils are immutable.

Metaphor: Like geological strata — each layer contains the remains of
species that lived and died. Paleontologists (future agencies) can study
these fossils to understand the past, but they cannot modify them.

Properties:
- Immutability: fossils cannot be modified after creation
- Stratification: time-based layers (epochs)
- Retrieval: indexed by species, biome, lobe, time range
- Compression: older strata are compressed to save space
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from egregore.domain.agency_taxonomy import AgencyState, Biome, Lobe, Species


@dataclass(frozen=True)
class FossilRegister:
    """Immutable record of a dead agency."""

    sediment_id: str
    agency_state: AgencyState
    stratum_epoch: str  # e.g., "2026-Q2", "2026-W24"
    fossilization_timestamp_ns: int
    compression_ratio: float = 1.0  # 1.0 = uncompressed


@dataclass
class Stratum:
    """A geological layer of fossils."""

    epoch: str
    fossils: list[FossilRegister] = field(default_factory=list)
    total_energy_consumed_j: float = 0.0
    total_work_units_processed: int = 0

    def add(self, fossil: FossilRegister) -> None:
        self.fossils.append(fossil)
        self.total_energy_consumed_j += fossil.agency_state.energy_consumed_j
        self.total_work_units_processed += fossil.agency_state.work_units_processed


class SedimentArchive:
    """
    Concrete sediment archive implementation.

    Manages fossil registers in stratified layers:
    - Current stratum is always open for new fossils
    - Older strata are compressed and sealed
    - Retrieval by species, biome, lobe, time range
    """

    def __init__(self, node_id: str = "pioneer1") -> None:
        self._node_id = node_id
        self._strata: dict[str, Stratum] = {}
        self._current_epoch = self._compute_epoch()
        self._fossil_count = 0

    def _compute_epoch(self) -> str:
        now = datetime.now(UTC)
        return f"{now.year}-Q{(now.month - 1) // 3 + 1}"

    def fossilize(self, agency: AgencyState) -> str:
        import time

        # Ensure current stratum exists
        epoch = self._compute_epoch()
        if epoch not in self._strata:
            self._strata[epoch] = Stratum(epoch=epoch)

        sediment_id = f"sediment_{self._node_id}_{epoch}_{self._fossil_count}"
        self._fossil_count += 1

        fossil = FossilRegister(
            sediment_id=sediment_id,
            agency_state=agency,
            stratum_epoch=epoch,
            fossilization_timestamp_ns=int(time.time() * 1e9),
        )

        self._strata[epoch].add(fossil)
        return sediment_id

    def query(
        self,
        species: Species | None = None,
        biome: Biome | None = None,
        lobe: Lobe | None = None,
        epoch: str | None = None,
    ) -> list[FossilRegister]:
        """Query fossils by criteria. All criteria are ANDed."""
        results = []
        strata_to_search = [self._strata[epoch]] if epoch else self._strata.values()

        for stratum in strata_to_search:
            for fossil in stratum.fossils:
                agency = fossil.agency_state
                if species and agency.agency_id.species != species:
                    continue
                if biome and agency.agency_id.biome != biome:
                    continue
                if lobe and agency.agency_id.lobe != lobe:
                    continue
                results.append(fossil)

        return results

    def compress_stratum(self, epoch: str) -> float:
        """Compress a sealed stratum. Returns compression ratio."""
        if epoch not in self._strata:
            return 1.0

        stratum = self._strata[epoch]
        # Simple compression: remove duplicate work unit patterns
        # In production: use canonical JSON + delta encoding
        original_count = len(stratum.fossils)
        # Dedup by agency_id (keep last fossil for each agency)
        seen = {}
        for fossil in stratum.fossils:
            key = fossil.agency_state.agency_id.raw
            seen[key] = fossil
        stratum.fossils = list(seen.values())

        compressed_count = len(stratum.fossils)
        ratio = compressed_count / original_count if original_count > 0 else 1.0

        for _fossil in stratum.fossils:
            # FossilRegister is frozen, so we can't modify it directly
            # In production, create a new compressed fossil
            pass

        return ratio

    def get_strata_summary(self) -> dict[str, Any]:
        return {
            epoch: {
                "fossil_count": len(s.fossils),
                "total_energy_j": s.total_energy_consumed_j,
                "total_work_units": s.total_work_units_processed,
            }
            for epoch, s in self._strata.items()
        }
