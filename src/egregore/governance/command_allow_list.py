"""
Command Allow-List — deterministic action authorization per node role.
"""
from __future__ import annotations
import logging, os
from pathlib import Path
from typing import Dict, List, Any
import yaml

logger = logging.getLogger(__name__)
DEFAULT_ALLOW_FILE = Path("config/command_allow_list.yaml")

class CommandAllowList:
    def __init__(self, file_path=DEFAULT_ALLOW_FILE):
        self.file_path = Path(file_path)
        self.rules = self._load_rules()

    def _load_rules(self):
        if not self.file_path.exists():
            logger.warning("Allow-list file not found at %s; using empty (deny all).", self.file_path)
            return {}
        with open(self.file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("Allow-list file must be a mapping.")
        return data.get("actions", {})

    def is_allowed(self, action, node_id, role="node"):
        rule = self.rules.get(action)
        if not rule:
            return False
        allowed_roles = rule.get("roles", [])
        allowed_nodes = rule.get("nodes", [])
        if "all" in allowed_roles or role in allowed_roles:
            if not allowed_nodes or node_id in allowed_nodes:
                return True
        return False

    def list_actions(self):
        return list(self.rules.keys())
