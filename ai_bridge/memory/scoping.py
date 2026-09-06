"""Stable memory boundaries for the configured package, identity and task facts."""

import hashlib
import json
from typing import Any


def memory_namespace(identity: Any, task: dict[str, Any], package_id: str = '') -> str:
    core = identity.core
    boundary = {
        'package_id': package_id,
        'identity_id': identity.identity_id,
        'identity': {field: getattr(core, field) for field in ('name', 'role', 'organization', 'mission')},
        'task_id': task.get('id'),
        'objective': task.get('objective'),
        'knowledge': task.get('knowledge', {}),
    }
    return hashlib.sha256(json.dumps(boundary, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
