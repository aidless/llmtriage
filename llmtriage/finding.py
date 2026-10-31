"""统一的结论对象：任何检查都产出 Finding，字段固定，便于机器消费。"""
from dataclasses import dataclass, field, asdict
from typing import Any

# 严重度：数值越大越"坏"。UNKNOWN 排在"正常"之上——宁可报警也不假装没事。
SEVERITY = {
    "OK": 0, "READY": 0, "GO": 0, "MEASURED": 0, "NO_ERROR_FOUND": 0,
    "UNKNOWN": 1,
    "DEGRADED": 2,
    "NOT_READY": 3, "NO_GO": 3, "FAKE_LEAD_SUBPROCESS_ERROR": 3, "GENUINE_PARENT_ERROR": 3,
}


@dataclass
class Finding:
    kind: str                 # gpu / probe / throughput / logs / budget
    verdict: str              # 见 SEVERITY 的键
    state: str                # OBSERVED / INFERRED / UNKNOWN
    evidence: str = ""        # 执行的命令或原始日志行，必须能复现
    detail: dict[str, Any] = field(default_factory=dict)

    # 各检查专用字段（保持扁平，方便测试与 JSON 消费）
    wall_clock_s: float | None = None
    required_s: float | None = None
    per_request_s: float | None = None
    child_pid: str | None = None
    models: list[str] | None = None
    ready: bool | None = None
    completion_tokens: int | None = None
    tok_per_s: float | None = None

    def severity(self) -> int:
        return SEVERITY.get(self.verdict, 1)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}
