#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bom_routing_core.py
===================
Turns a Product Attribute Master (a.k.a. product structure - the indented
"staircase" sheet) into an SAP-format BOM CSV and routing CSV.

Built to survive the master being edited by hand:

* **Any number of levels.**  The level columns are found from the *shape of the
  data* - a run of adjacent columns where every row puts its part number in
  exactly one of them - so a part with levels 0-3 and one with 0-7 both work,
  and the level headers can be renamed freely ("lvl 2", "Level 2",
  "Sub-Assy No (L2)", "Child Part No" ...).  "lvl N"-style header names are
  used as a back-up signal.
* **Headers matched loosely.**  Qty, UoM, Op Seq, Workcentre etc. are matched
  by normalised wording + typo tolerance, not exact text, and notes in
  brackets are ignored.  A title row above the header or extra columns are
  fine.
* **Nothing assumed.**  Only values present in the master are written; every
  other SAP column is left blank.  (Generated: BOM item numbers 0010, 0020 ...
  per parent, in sheet order - SAP's default step.)

Library use:   result = convert("master.xlsx")
Command line:  python bom_routing_core.py master.xlsx --out-dir . --part 13883726
"""

from __future__ import annotations

import csv
import difflib
import io
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# SAP output layouts (identical to the reference BOM / routing extracts)
# ---------------------------------------------------------------------------
BOM_HEADER = ["Explosion level", "Item Number", "Component number", "Object description",
              "Overflow indicator", "Comp. Qty (CUn)", "Component unit", "Item category",
              "Exception", "Requirement Segment", "Stock Segment"]

ROUTING_HEADER = ["Material", "Plant", "Material Description", "Task List Type", "Group",
                  "Group Counter", "Sequence", "Activity", "Suboperation", "Valid From",
                  "Cost Center", "Cost Element", "Work center", "Operation short text",
                  "Base Quantity",
                  "Activity Type", "Standard value unit", "Standard Value",
                  "Activity Type", "Standard value unit", "Standard Value",
                  "Activity Type", "Standard value unit", "Standard Value"]

# ---------------------------------------------------------------------------
# Header vocabulary - extend freely.  Matching ignores case, punctuation and
# anything in brackets, and tolerates small typos.
# ---------------------------------------------------------------------------
FIELD_ALIASES = {
    "op_seq":      ["op seq", "op sequence", "operation seq", "operation sequence", "opseq",
                    "op no", "operation no", "op number", "operation number", "op seq no",
                    "seq no", "sequence", "seq", "op"],
    "workcentre":  ["workcentre", "work centre", "work center", "workcenter", "workcentres",
                    "work centres", "work centers", "workcentre no", "work centre no",
                    "work center no", "wc", "wc no", "machine"],
    "op_name":     ["operation", "operation description", "operation name", "operation text",
                    "operation short text", "op description", "op name", "op text", "process",
                    "process name"],
    "qty":         ["qty", "quantity", "comp qty", "component qty", "component quantity",
                    "qty per", "qty per parent", "qty parent", "req qty", "usage qty", "nos"],
    "uom":         ["uom", "unit", "units", "unit of measure", "base unit", "component unit",
                    "um", "uom unit"],
    "description": ["description", "part description", "material description",
                    "object description", "desc", "part name", "component description"],
    "plant":       ["plant", "plnt"],
    "item_cat":    ["item cat", "item category", "itm cat", "item categ"],
}
FIELD_LABEL = {"op_seq": "Op Seq", "workcentre": "Workcentre", "op_name": "Operation name",
               "qty": "Qty", "uom": "UoM", "description": "Description", "plant": "Plant",
               "item_cat": "Item category"}

BLANK_IN_LEVEL = {"", "-", "—", "–", "na", "n/a"}
MIN_ALONE_SHARE = 0.5      # a level column must introduce a part on >= half of its rows
FUZZY_MIN = 0.82            # minimum similarity for a typo-tolerant header match


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def clean(v) -> str:
    """Cell -> trimmed string; whole floats lose their '.0'."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if hasattr(v, "strftime"):
        return v.strftime("%d.%m.%Y")
    return str(v).strip()


def is_blank(v) -> bool:
    return clean(v) == ""


def norm_header(h) -> str:
    s = clean(h).lower()
    s = re.sub(r"\(.*?\)", " ", s)        # drop bracketed notes "(please fill)"
    s = re.sub(r"\(.*$", " ", s)          # ...and unclosed ones "(please fi"
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_LEVEL_NAME = [
    re.compile(r"^\s*(lvl|level|lev|lv)\s*[-_.#:]?\s*\d+", re.I),
    re.compile(r"^\s*l\s*[-_]?\s*\d+\b", re.I),
    re.compile(r"\(\s*(l|lvl|level)\s*[-_]?\s*\d+\s*\)", re.I),
]


def is_level_name(h) -> bool:
    s = clean(h)
    return any(p.search(s) for p in _LEVEL_NAME)


def numeric_like(s: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+(\.\d+)?", s.strip())) if s else False


def to_number(v):
    """Numeric cell/text -> int/float; anything else returned as cleaned text."""
    s = clean(v)
    if not s:
        return ""
    if numeric_like(s):
        f = float(s)
        return int(f) if f.is_integer() else f
    return s


def fmt_number(v) -> str:
    if v == "" or v is None:
        return ""
    if isinstance(v, float):
        return repr(v) if not v.is_integer() else str(int(v))
    return str(v)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def read_sheets(path: str):
    """[(sheet_name, rows)] - rows are lists of raw cell values."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        out = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
        wb.close()
        return out
    if ext == ".csv":
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        return [("csv", [list(r) for r in csv.reader(io.StringIO(text))])]
    if ext == ".xls":
        raise ValueError("Old .xls format isn't supported - please save the master as .xlsx.")
    raise ValueError(f"Unsupported file type '{ext}'. Upload an .xlsx (or .csv) master.")


# ---------------------------------------------------------------------------
# layout detection
# ---------------------------------------------------------------------------
@dataclass
class Layout:
    sheet: str
    header_row: int                 # 0-based index into rows
    level_cols: list                # column indexes, left -> right = shallow -> deep
    fields: dict                    # field -> column index
    name_cols: list                 # unlabeled columns right of Workcentre (op names)
    headers: list


def _header_row(rows, limit=25) -> int:
    best, best_score = 0, -1
    for i, r in enumerate(rows[:limit]):
        score = sum(1 for c in r if isinstance(c, str) and c.strip() and not numeric_like(c))
        if score > best_score:
            best, best_score = i, score
    return best


def _shape_block(data, ncols):
    """Widest run of adjacent columns forming the staircase.

    A column belongs to the run when most of its values *introduce* a part on
    their own row (no id further left in the run) - attribute columns such as
    revision or drawing no. always sit beside an id, so they fail this test even
    when sparsely filled.  Columns of small integers (op seq, qty) are never
    level columns.  Empty columns inside the run are allowed (unused level)."""
    vals = [[clean(r[c]) if c < len(r) else "" for c in range(ncols)] for r in data]
    has = [[v.lower() not in BLANK_IN_LEVEL for v in row] for row in vals]
    keep = [i for i, h in enumerate(has) if any(h)]
    vals, has = [vals[i] for i in keep], [has[i] for i in keep]
    if not has:
        return None
    colcount = [sum(h[c] for h in has) for c in range(ncols)]

    def id_like(c):
        """Part numbers: not small integers (op seq / qty), not free text (remarks)."""
        v = [row[c] for row, h in zip(vals, has) if h[c]]
        if not v:
            return False
        short = sum(1 for x in v if numeric_like(x) and len(x.split(".")[0].lstrip("+-")) <= 4)
        wordy = sum(1 for x in v if len(x.split()) > 2 or len(x) > 40)
        return short <= 0.5 * len(v) and wordy <= 0.2 * len(v)

    cands = []
    for s in range(ncols):
        if colcount[s] == 0 or not id_like(s):
            continue
        last = s
        for c in range(s + 1, ncols):
            if colcount[c] == 0:
                continue
            alone = sum(1 for h in has if h[c] and not any(h[s:c]))
            if alone < MIN_ALONE_SHARE * colcount[c] or not id_like(c):
                break
            last = c
        if last == s:
            continue
        # root rule: the first id in the run must sit in its first column
        # (the top-level part is listed before any of its children)
        first = next(h for h in has if any(h[s:last + 1]))
        if first[s]:
            cands.append((s, last))
    if not cands:
        return None
    return max(cands, key=lambda c: (c[1] - c[0], -c[0]))


def _match_fields(headers, exclude):
    """Greedy best-score assignment of header columns to known fields."""
    scored = []
    for ci, h in enumerate(headers):
        if ci in exclude:
            continue
        n = norm_header(h)
        if not n:
            continue
        for fld, aliases in FIELD_ALIASES.items():
            best = 0.0
            for a in aliases:
                if n == a:
                    sc = 1.0
                elif n.startswith(a + " ") and len(a) >= 3:
                    sc = 0.93
                else:
                    r = difflib.SequenceMatcher(None, n, a).ratio()
                    sc = r * 0.9 if r >= FUZZY_MIN else 0.0
                best = max(best, sc)
            if best:
                scored.append((best, ci, fld))
    fields, used = {}, set()
    for sc, ci, fld in sorted(scored, key=lambda t: (-t[0], t[1])):
        if fld in fields or ci in used:
            continue
        fields[fld] = ci
        used.add(ci)
    return fields


def detect_layout(sheets) -> Layout:
    best = None
    for name, rows in sheets:
        if not rows:
            continue
        hr = _header_row(rows)
        headers = rows[hr]
        ncols = max((len(r) for r in rows), default=0)
        headers = list(headers) + [None] * (ncols - len(headers))
        data = rows[hr + 1:]
        named = [i for i, h in enumerate(headers) if is_level_name(h)]
        shape = _shape_block(data, ncols)
        if len(named) >= 2:
            lo, hi = min(named), max(named)
            if shape and not (shape[1] < lo or shape[0] > hi):
                lo, hi = min(lo, shape[0]), max(hi, shape[1])
        elif shape:
            lo, hi = shape
        else:
            continue
        width = hi - lo + 1
        if best is None or width > best[0]:
            best = (width, name, hr, headers, list(range(lo, hi + 1)))
    if best is None:
        raise ValueError("Couldn't find the level columns. Expected a staircase layout: one "
                         "column per level (level 0, 1, 2 ...) with each row's part number in "
                         "exactly one of them.")
    _, name, hr, headers, levels = best
    fields = _match_fields(headers, set(levels))
    name_cols = []
    if "workcentre" in fields:
        for j in range(fields["workcentre"] + 1, len(headers)):
            if clean(headers[j]) == "":
                name_cols.append(j)
            else:
                break
    return Layout(name, hr, levels, fields, name_cols, headers)


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------
@dataclass
class Node:
    pid: str
    pos: int                 # level-column position (0 = level 0)
    depth: int               # true depth in the tree (handles skipped columns)
    row: int                 # 1-based sheet row where the part first appears
    qty: object = ""
    uom: str = ""
    desc: str = ""
    item_cat: str = ""
    plant: str = ""
    item_no: str = ""
    ops: list = field(default_factory=list)       # [(activity, workcentre, short_text)]
    children: list = field(default_factory=list)


def _row_ops(r, lay: Layout):
    g = lambda f: clean(r[lay.fields[f]]) if f in lay.fields and lay.fields[f] < len(r) else ""
    seq, wc, name = g("op_seq"), g("workcentre"), g("op_name")
    if not name:
        name = next((clean(r[j]) for j in lay.name_cols if j < len(r) and clean(r[j])), "")
    if wc and not numeric_like(wc) and not name:
        wc, name = "", wc                    # a name written in the workcentre column
    if not (seq or wc or name):
        return []
    if not seq and re.search(r"[;\n]", name):
        return [("", "", n.strip()) for n in re.split(r"[;\n]+", name) if n.strip()]
    if numeric_like(seq) and float(seq).is_integer() and 0 <= float(seq) <= 9999:
        seq = f"{int(float(seq)):04d}"
    return [(seq, wc, name)]


def build_tree(rows, lay: Layout):
    roots, nodes, warnings = [], [], []
    last_at = {}                 # level position -> node
    current = None
    multi = []
    for ri in range(lay.header_row + 1, len(rows)):
        r = rows[ri]
        ids = [(p, clean(r[c])) for p, c in enumerate(lay.level_cols)
               if c < len(r) and clean(r[c]).lower() not in BLANK_IN_LEVEL]
        if not ids:
            if current is not None:
                current.ops += _row_ops(r, lay)      # continuation row: extra operation
            continue
        pos, pid = ids[0]
        if len(ids) > 1:
            multi.append(f"row {ri + 1} ({pid} + {', '.join(v for _, v in ids[1:])})")
        if current is not None and current.pos == pos and current.pid == pid:
            current.ops += _row_ops(r, lay)          # same part repeated = next operation
            continue
        parent = next((last_at[p] for p in range(pos - 1, -1, -1) if p in last_at), None)
        g = lambda f: r[lay.fields[f]] if f in lay.fields and lay.fields[f] < len(r) else None
        node = Node(pid, pos, (parent.depth + 1) if parent else 0, ri + 1,
                    qty=to_number(g("qty")), uom=clean(g("uom")), desc=clean(g("description")),
                    item_cat=clean(g("item_cat")), plant=clean(g("plant")))
        node.ops += _row_ops(r, lay)
        if parent is None:
            if pos != 0:
                warnings.append(f"Row {ri + 1}: '{pid}' has no parent above it - treated as a top-level part.")
            roots.append(node)
        else:
            parent.children.append(node)
            node.item_no = f"{len(parent.children) * 10:04d}"
        last_at[pos] = node
        for p in [x for x in last_at if x > pos]:
            del last_at[p]
        nodes.append(node)
        current = node
    if multi:
        warnings.append(f"{len(multi)} row(s) have a value in more than one level column; the "
                        f"left-most was used as the part: " + "; ".join(multi[:5])
                        + (" ..." if len(multi) > 5 else ""))
    if len(roots) > 1:
        warnings.append(f"{len(roots)} top-level (level 0) parts found: "
                        + ", ".join(n.pid for n in roots[:6]) + (" ..." if len(roots) > 6 else "")
                        + ". The BOM explodes each of them.")
    return roots, nodes, warnings


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------
def bom_rows(roots):
    out = [BOM_HEADER]

    def walk(n):
        for c in n.children:
            line = [""] * len(BOM_HEADER)
            line[0] = "." * c.depth + str(c.depth)
            line[1] = c.item_no
            line[2] = c.pid
            line[3] = c.desc
            line[5] = fmt_number(c.qty)
            line[6] = c.uom
            line[7] = c.item_cat
            out.append(line)
            walk(c)
    for r in roots:
        walk(r)
    return out


def routing_rows(nodes):
    out, seen = [ROUTING_HEADER], set()
    for n in nodes:
        if n.pid in seen:
            continue
        seen.add(n.pid)
        for act, wc, text in n.ops:
            line = [""] * len(ROUTING_HEADER)
            line[0], line[1], line[2] = n.pid, n.plant, n.desc
            line[7], line[12], line[13] = act, wc, text
            out.append(line)
    return out


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)          # CRLF line ends, like the SAP extracts


@dataclass
class Result:
    bom: list
    routing: list
    summary: dict
    warnings: list


def convert(path: str) -> Result:
    sheets = read_sheets(path)
    lay = detect_layout(sheets)
    rows = dict(sheets)[lay.sheet]
    roots, nodes, warnings = build_tree(rows, lay)
    bom, routing = bom_rows(roots), routing_rows(nodes)

    detected = {FIELD_LABEL[f]: clean(lay.headers[c]) or f"column {c + 1}" for f, c in lay.fields.items()}
    if lay.name_cols:
        detected["Operation name"] = detected.get("Operation name") or "unlabeled column(s) next to Workcentre"
    for f, msg in [("qty", "No quantity column found - Comp. Qty left blank."),
                   ("uom", "No unit (UoM) column found - Component unit left blank.")]:
        if f not in lay.fields:
            warnings.append(msg)
    if not any(f in lay.fields for f in ("op_seq", "workcentre", "op_name")) and not lay.name_cols:
        warnings.append("No operation columns (Op Seq / Workcentre) found - routing is empty.")
    elif "op_seq" not in lay.fields:
        warnings.append("No Op Seq column found - Activity left blank.")
    no_ops = sum(1 for n in {n.pid: n for n in nodes}.values() if not n.ops)
    max_depth = max((n.depth for n in nodes), default=0)
    summary = {
        "sheet": lay.sheet,
        "header_row": lay.header_row + 1,
        "level_columns": [clean(lay.headers[c]) or f"column {c + 1}" for c in lay.level_cols],
        "deepest_level": max_depth,
        "top_level_parts": [n.pid for n in roots],
        "bom_lines": len(bom) - 1,
        "routing_lines": len(routing) - 1,
        "materials_with_operations": len({n.pid for n in nodes if n.ops}),
        "materials_without_operations": no_ops,
        "detected_columns": detected,
    }
    return Result(bom, routing, summary, warnings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "part"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Product attribute master -> SAP BOM + routing CSV")
    ap.add_argument("master")
    ap.add_argument("--part", help="part name for the output file names (default: level-0 part)")
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args(argv)
    res = convert(a.master)
    part = _safe(a.part or (res.summary["top_level_parts"] or ["part"])[0])
    os.makedirs(a.out_dir, exist_ok=True)
    bp, rp = os.path.join(a.out_dir, f"BOM_{part}.csv"), os.path.join(a.out_dir, f"ROUTING_{part}.csv")
    write_csv(res.bom, bp)
    write_csv(res.routing, rp)
    s = res.summary
    print(f"Levels: {len(s['level_columns'])} columns, deepest level {s['deepest_level']} | "
          f"BOM {s['bom_lines']} lines | routing {s['routing_lines']} lines")
    print("Detected:", s["detected_columns"])
    for w in res.warnings:
        print("Note:", w)
    print("->", bp, "|", rp)


if __name__ == "__main__":
    main()
