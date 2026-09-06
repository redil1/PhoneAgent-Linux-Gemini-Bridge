"""Inventory ProviderConfig field coverage without loading configuration or credentials.

This is a scope report, not a release gate. Matching schema fields still require
activation, worker, persistence and rollback evidence. Environment-only controls
and RuntimeConfig are outside this report and require their own inventory.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


def declared_fields(path: Path, class_name: str) -> set[str]:
    classes = {node.name: node for node in ast.parse(path.read_text()).body
               if isinstance(node, ast.ClassDef)}

    def collect(name: str) -> set[str]:
        node = classes.get(name)
        if node is None:
            return set()
        fields = {item.target.id for item in node.body
                  if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
        for base in node.bases:
            if isinstance(base, ast.Name):
                fields.update(collect(base.id))
        return fields

    return collect(class_name)


def audit(root: Path) -> dict[str, object]:
    provider = declared_fields(root / "ai_bridge/runtime_config.py", "ProviderConfig")
    packaged = declared_fields(root / "ai_bridge/control_plane.py", "RuntimeControl") & provider
    missing = provider - packaged
    secrets = {name for name in missing if name.endswith("_api_key")}
    credential_module = ast.parse((root / "ai_bridge/provider_credentials.py").read_text())
    bindings = next(ast.literal_eval(node.value) for node in credential_module.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "_DEFAULTS" for target in node.targets))
    referenced = secrets & set(bindings) if "credential_refs" in packaged else set()
    legacy = {name for name in missing if name.startswith("chatgpt_realtime_")}
    host = {name for name in missing - secrets - legacy
            if name.endswith(("_binary", "_base_url", "_model_path"))}
    behavior = missing - secrets - legacy - host
    groups = {
        "present_in_package_schema": packaged,
        "private_fields_with_reference_bindings": referenced,
        "needs_secret_reference_policy": secrets - referenced,
        "needs_legacy_removal": legacy,
        "needs_host_profile_treatment": host,
        "needs_behavior_setting_roundtrip": behavior,
    }
    assert set().union(*groups.values()) == provider
    assert sum(map(len, groups.values())) == len(provider)
    return {
        "scope": "ProviderConfig field names only; no configuration values loaded",
        "completion_proven": False,
        "provider_field_count": len(provider),
        "uncovered_field_count": len(missing - referenced),
        "groups": {name: {"count": len(fields), "fields": sorted(fields)}
                   for name, fields in groups.items()},
    }


if __name__ == "__main__":
    print(json.dumps(audit(Path(__file__).resolve().parents[1]), indent=2))
