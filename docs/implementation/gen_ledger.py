"""Generate the finding ledger from root-causes.md's cross-reference table.

Mechanical: every audit finding ID must appear exactly once, mapped to the
stage where it is expected to close. Fails loudly on any ID it cannot map,
so a finding can never be silently dropped from the ledger.

Recorded statuses survive regeneration. The first version of this script
emitted every row as `open`, which meant regenerating after a stage landed
would silently erase every closure the trail had recorded — the exact thing
the ledger exists to prevent. Statuses are now read back out of the target
file and carried forward; a row that had a non-`open` status and can no
longer be mapped is an error, not a silent drop.

Usage: python3 gen_ledger.py   (rewrites the ledger table inside progress.md)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "docs/architecture/audit"
RC_DOC = ROOT / "docs/architecture/root-causes.md"

# RC -> stage where findings of that root cause are expected to close.
RC_STAGE = {
    "RC1": "S2", "RC2": "S3", "RC3": "S7", "RC4": "S5", "RC5": "S8",
    "RC6": "S9", "RC7": "S10", "RC8": "S11", "RC9": "S12", "RC10": "S14",
    "RC11": "S13", "RC12": "S16", "RC13": "S5",
}
# RC-10 splits: the AppContext + security-boundary items land early (S4),
# the rest of the web work lands in S14.
RC10_EARLY = {"MANAGER-1", "MANAGER-4", "MANAGER-15", "WEB-1", "WEB-3",
              "WEB-10", "WEB-20", "ERRORS-12"}
# D-11 purge (runtime serial reconnect) and D-9 default-view: Stage 1.
STAGE1 = {"SERIAL-14", "PYSIDE-13", "DC-14", "STEPPER-12", "MANAGER-14"}
SPECIAL = {"LOCAL-OK": "S15", "doc": "S0"}
# Findings whose stage was reassigned during execution, against what the RC
# mapping would compute. Without this, regeneration silently reverts the
# reassignment and the ledger disagrees with the session log that recorded it.
STAGE_OVERRIDE = {
    # Shares `web_server.py` with WEB-14/21, so it could not sit in the
    # S15 write set; moved S15 -> S14 when S14/S15 were partitioned.
    "WEB-16": "S14",
}


def audit_ids():
    ids = set()
    for f in sorted(AUDIT.glob("*.md")):
        for m in re.finditer(r"^### ([A-Z]+(?:-[A-Z]+)?-\d+)", f.read_text(), re.M):
            ids.add(m.group(1))
    return ids


def parse_xref():
    """Parse rows like: | MANAGER | 1 RC1/RC10 · 2 RC1/RC10 · 3 RC1 · ... |"""
    text = RC_DOC.read_text()
    block = text.split("## Finding → root-cause cross-reference")[1]
    mapping = {}
    for line in block.splitlines():
        if not line.startswith("|") or line.startswith("|---") or "Mapping" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 2:
            continue
        prefix, body = cells
        if not re.fullmatch(r"[A-Z]+(-[A-Z]+)?", prefix):
            continue
        for entry in body.split("·"):
            entry = entry.strip()
            m = re.match(r"^(\d+)\s+(.*)$", entry)
            if not m:
                continue
            fid = f"{prefix}-{m.group(1)}"
            mapping[fid] = m.group(2).strip()
    return mapping


def stage_for(fid, raw):
    tags = re.findall(r"RC\d+|LOCAL-OK|doc", raw)
    if fid in STAGE_OVERRIDE:
        return STAGE_OVERRIDE[fid], tags
    if fid in STAGE1:
        return "S1", tags
    for t in tags:
        if t == "RC10":
            return ("S4" if fid in RC10_EARLY else "S14"), tags
        if t in RC_STAGE:
            return RC_STAGE[t], tags
    for t in tags:
        if t in SPECIAL:
            return SPECIAL[t], tags
    return None, tags


LEDGER_ROW = re.compile(r"^\|\s*([A-Z][A-Z-]*-\d+)\s*\|[^|]*\|[^|]*\|[^|]*\|\s*(.+?)\s*\|$")


def existing_statuses(path):
    """Statuses already recorded in the ledger, so regeneration preserves them."""
    if not path.exists():
        return {}
    statuses = {}
    for line in path.read_text().splitlines():
        m = LEDGER_ROW.match(line)
        if m:
            statuses[m.group(1)] = m.group(2)
    return statuses


def main():
    ids, xref = audit_ids(), parse_xref()
    missing = ids - set(xref)
    extra = set(xref) - ids
    if missing:
        sys.exit(f"UNMAPPED findings (fix root-causes.md xref): {sorted(missing)}")
    if extra:
        sys.exit(f"xref cites nonexistent findings: {sorted(extra)}")

    rows, unresolved = [], []
    for fid in sorted(ids, key=lambda s: (s.rsplit("-", 1)[0], int(s.rsplit("-", 1)[1]))):
        raw = xref[fid]
        stage, tags = stage_for(fid, raw)
        if stage is None:
            unresolved.append((fid, raw))
            continue
        how = "explicit" if stage in ("S15", "S16") or "LOCAL-OK" in tags else "root cause"
        rows.append((fid, " / ".join(tags) or raw, stage, how))
    if unresolved:
        sys.exit(f"No stage for: {unresolved}")

    target = Path(__file__).resolve().parent / "progress.md"
    prior = existing_statuses(target)
    lost = {f: s for f, s in prior.items() if s != "open" and f not in ids}
    if lost:
        sys.exit(f"recorded closures would be dropped: {sorted(lost)}")

    table = ["| Finding | Root cause | Stage | Closed by | Status |",
             "|---|---|---|---|---|"]
    table += [f"| {f} | {t} | {s} | {h} | {prior.get(f, 'open')} |"
              for f, t, s, h in rows]

    doc = target.read_text().splitlines()
    start = next(i for i, ln in enumerate(doc) if ln.startswith("| Finding |"))
    end = start
    while end < len(doc) and doc[end].startswith("|"):
        end += 1
    target.write_text("\n".join(doc[:start] + table + doc[end:]) + "\n")
    kept = sum(1 for f, *_ in rows if prior.get(f, "open") != "open")
    print(f"{kept} recorded closures preserved")

    counts = {}
    for _, _, s, _ in rows:
        counts[s] = counts.get(s, 0) + 1
    print(f"{len(rows)} findings mapped")
    for s in sorted(counts, key=lambda x: int(x[1:])):
        print(f"  {s}: {counts[s]}")


main()
