from __future__ import annotations

from egregore.domain.provenance_model import ProvenanceEvent
from egregore.interface.provenance_port import IProvenanceSink, IProvenanceVerifier
from egregore.kernel.provenance import Provenance


class ZarcProvenanceSink(IProvenanceSink, IProvenanceVerifier):
    """
    Infrastructure adapter: bridges domain ProvenanceEvent -> kernel.Provenance.append()
    while preserving the existing `.zarc` JSONL chain format.
    """

    def __init__(self, *, provenance: Provenance) -> None:
        self._provenance = provenance

    def append(self, event: ProvenanceEvent) -> None:
        self._provenance.append(
            engine=event.engine,
            event=event.event,
            payload=event.payload,
            ts_ns=event.ts_ns,
        )

    def verify_chain(self) -> bool:
        return self._provenance.verify_chain()
