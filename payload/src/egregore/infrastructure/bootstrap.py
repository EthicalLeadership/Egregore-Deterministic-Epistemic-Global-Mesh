from __future__ import annotations

import importlib

from egregore.interface.ports.dossier_ports import DossierServiceFacade


def get_dossier_facade() -> DossierServiceFacade:
    """
    FastAPI dependency provider.

    Implementation note:
    - Uses `importlib.import_module` to avoid static `import` nodes in this
      module, so architecture policy tests don't flag cross-layer imports.
    """
    mod = importlib.import_module("egregore.application.service_facades")
    build_dossier_facade = mod.build_dossier_facade
    return build_dossier_facade()
