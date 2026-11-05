"""Install docs must match packaging metadata, and hardware claims must match files.

Red when README.md or README.zh.md tells a reader to install this project from
PyPI while [tool.llmtriage] distribution is not "pypi". Also pins the evidence
boundary the README is allowed to state: one stored idle rocm-smi CSV, no
gfx target, no "we never ran AMD hardware" sentence.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READMES = ("README.md", "README.zh.md")

# An install command. The comment after '#' is classified separately.
_PIP_INSTALL = re.compile(
    r"(?:python\s+-m\s+)?pip3?\s+install\s+(?P<rest>[^#\n]*)",
    re.I,
)
_NEGATION = re.compile(
    r"not on pypi|not published|is not published|do not |don't |"
    r"未发布|未上架|不在\s*pypi|不要",
    re.I,
)


def _distribution() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^distribution\s*=\s*"([^"]+)"\s*$', text)
    assert m, 'pyproject.toml must set distribution = "source" or "pypi"'
    assert m.group(1) in {"source", "pypi"}, m.group(1)
    return m.group(1)


def pypi_claim_lines(text: str) -> list[str]:
    """Lines that tell a reader this project is installable from PyPI.

    Editable installs and git+ URLs are the source path and are not claims.
    A denial ("not on PyPI", "不要") suppresses prose mentions on that line.
    It does not suppress an actual `pip install llmtriage` command.
    """
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        flagged = False
        for match in _PIP_INSTALL.finditer(line):
            code = match.group("rest")
            if re.search(r"(^|\s)(-e|--editable)(\s|$)", code) or "git+" in code:
                continue
            if re.search(r"(^|[\s\"'])llmtriage([\s\"']|$)", code):
                bad.append("%d: %s" % (lineno, line.strip()))
                flagged = True
        if flagged or _NEGATION.search(line):
            continue
        if re.search(r"from PyPI|从 PyPI", line):
            bad.append("%d: %s" % (lineno, line.strip()))
        if re.search(r"pypi\.org/(?:project|pypi)/llmtriage", line, re.I):
            if not re.search(r"404|not found", line, re.I):
                bad.append("%d: %s" % (lineno, line.strip()))
        if re.search(r"shields\.io/pypi|badge/pypi", line, re.I):
            bad.append("%d: %s" % (lineno, line.strip()))
    return bad


def test_detector_flags_index_install_and_allows_source_install():
    assert pypi_claim_lines("pip install llmtriage\n")
    assert pypi_claim_lines("pip install llmtriage  # from PyPI\n")
    assert pypi_claim_lines("pip install llmtriage  # not on PyPI\n")
    assert not pypi_claim_lines('python -m pip install -e ".[dev]"\n')
    assert not pypi_claim_lines('python -m pip install -e .\n')
    assert not pypi_claim_lines(
        'python -m pip install "git+https://github.com/aidless/llmtriage.git"\n'
    )
    assert not pypi_claim_lines(
        "Not on PyPI. https://pypi.org/pypi/llmtriage/json returned 404.\n"
    )
    assert pypi_claim_lines("Install from PyPI when the index has it.\n")
    assert pypi_claim_lines("https://pypi.org/project/llmtriage is the project page.\n")


def test_readme_pypi_claim_matches_metadata():
    dist = _distribution()
    claims: list[str] = []
    for name in READMES:
        text = (ROOT / name).read_text(encoding="utf-8")
        for hit in pypi_claim_lines(text):
            claims.append("%s:%s" % (name, hit))
    if dist != "pypi":
        assert not claims, (
            "distribution=%r but a README claims a PyPI install:\n%s"
            % (dist, "\n".join(claims))
        )
    else:
        assert claims, 'distribution="pypi" but neither README documents an index install'


def test_readme_status_version_matches_pyproject():
    py = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'(?m)^version\s*=\s*"([^"]+)"', py)
    assert version
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    status = re.search(r"Status:\s*\*\*([0-9]+\.[0-9]+\.[0-9]+)", readme)
    assert status, "README status line must name the package version"
    assert status.group(1) == version.group(1), (status.group(1), version.group(1))


def test_readme_hardware_claim_matches_stored_evidence():
    en = (ROOT / "README.md").read_text(encoding="utf-8")
    zh = (ROOT / "README.zh.md").read_text(encoding="utf-8")
    for name, text in (("README.md", en), ("README.zh.md", zh)):
        assert "not yet run on physical AMD" not in text, name
        assert "未在真实 AMD 硬件上实跑" not in text, name
        assert "gfx1100" not in text, name
        assert "rocm_smi_w7900_2026-11-01.csv" in text, name
        assert "evidence-rocm-w7900-2026-11-01.md" in text, name
    assert "## Evidence boundaries" in en
    assert "## Failure modes" in en
    assert "OBSERVED" in en and "INFERRED" in en and "UNKNOWN" in en
    assert ".github/workflows/ci.yml" in en
    assert "showproductname" in en
    assert "in_use" in en
    assert "What this tool does not prove" in en
    assert "twine" not in en.lower()
