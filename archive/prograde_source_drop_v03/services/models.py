from dataclasses import dataclass, field
from typing import List


@dataclass
class Violation:
    severity: str           # 'error' | 'warning' | 'info'
    rule_code: str
    message: str
    position_ids: List[str] = field(default_factory=list)
    suggested_fix: str = ""
