"""Left-to-right program ERD, drawn with cli-anything-drawio.

Usage:
    python build_erd.py <model.json> <sections.json | -> <cli exe> <output.drawio> <commands out>

Layout rules:
  * Sources (SAP standard CDS views, DB tables) in the left-most column.
  * Final output entities (frontend tabs; without a frontend: exposed entities
    nothing else reads) alone in the right-most column.
  * Background bands: Source / CDS View / 최종 출력.
  * With a sections.json, the CDS View band is split into functional sections
    (horizontal lanes). Pass "-" to draw it without inner sections.
  * Views that aggregate show what they aggregate, the value they produce
    and where that value is used.

This script only computes positions. Every shape and connector is created by
the CLI (commands are piped into its REPL).
"""
import html
import json
import math
import os
import re
import subprocess
import sys

model_path, sections_path, cli_exe, out_path, script_path = sys.argv[1:6]
M = json.load(open(model_path, encoding="utf-8"))
CONFIG = (json.load(open(sections_path, encoding="utf-8"))
          if sections_path != "-" and os.path.isfile(sections_path) else {})
SECTIONS = list(CONFIG.get("sections", []))
HAS_SECTIONS = bool(SECTIONS)
AGG_NOTES = CONFIG.get("aggregation_notes", {})
SCREEN = M["screen"]
N = {n["name"]: n for n in M["nodes"]}
EDGES = [e for e in M["edges"] if e["src"] != e["dst"]]
if not EDGES:
    sys.exit("모델에 관계가 하나도 없다. 패키지 경로와 screen.json을 확인한다.")

HAS_TABS = any(t["entity"] in N for t in SCREEN.get("tabs", []))
OP_TABLES = {t["entity"]: t for t in SCREEN.get("object_page_tables", [])}
EXTRA = {t["entity"]: t for t in SCREEN.get("extra_entities", [])}
EXPOSED = set(SCREEN.get("service_exposed", []))

# Final output entities: frontend tabs, else what screen.json lists under
# "outputs", else exposed program views that no other view reads.
_read_by = {n: 0 for n in N}
for e in EDGES:
    _read_by[e["dst"]] += 1
OUT = {}
for i, t in enumerate(SCREEN.get("tabs", [])):
    if t["entity"] in N and t["entity"] not in OUT:
        OUT[t["entity"]] = {"badge": f"화면 탭{i + 1}", "title": t.get("title", "")}
for t in SCREEN.get("outputs", []):
    if t["entity"] in N and t["entity"] not in OUT:
        OUT[t["entity"]] = {"badge": "최종 출력", "title": t.get("title", "")}
if not OUT:
    top = [n for n in N if N[n]["category"] == "program" and not _read_by[n]]
    for n in ([x for x in top if x in EXPOSED] or top):
        OUT[n] = {"badge": "최종 출력",
                  "title": "서비스 노출" if n in EXPOSED else "다른 view가 읽지 않는 최상위 view"}
OUT_ORDER = {n: i for i, n in enumerate(OUT)}
A = M.get("abstract", {})
EXT_PAGE = 3 if A else 2

cmds = []


def q(text):
    assert '"' not in text and "\n" not in text, text
    return f'"{text}"'


def esc(text):
    return html.escape(text or "", quote=False).replace('"', "&quot;")


def cid(name):
    return name.lower()


# ── node appearance ──────────────────────────────────────────────────

COLORS = {"R": ("#dae8fc", "#6c8ebf"), "I": ("#d5e8d4", "#82b366"),
          "C": ("#fff2cc", "#d6b656"), "P": ("#e1d5e7", "#9673a6"),
          "A": ("#f8cecc", "#b85450")}
ROW_STYLE = ("text;html=1;strokeColor=none;fillColor=none;align=left;"
             "verticalAlign=middle;spacingLeft=8;spacingRight=4;overflow=hidden;"
             "rotatable=0;fontSize=11;whiteSpace=wrap;")
HEAD_H, ROW_H, BOX_W = 58, 20, 340
FUNC_KO = {"SUM": "합계", "MAX": "최댓값", "MIN": "최솟값", "AVG": "평균", "COUNT": "건수"}
AGG_COLOR = "#7030a0"


def row(markup):
    """A table row; its height grows with the amount of text."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", markup))
    units = sum(1.75 if ord(c) > 0x2E80 else 1 for c in plain)
    return markup, ROW_H * max(1, math.ceil(units / 46))


def upper_funcs(expr):
    return re.sub(r"\b(sum|max|min|avg|count)\(", lambda m: m.group(1).upper() + "(",
                  expr, flags=re.I)


def aggregation_rows(name, agg):
    """What is aggregated, what value comes out, and where that value is used."""
    rows = [row(f"<b style='color:{AGG_COLOR}'>Σ 집계</b>&nbsp;&nbsp;GROUP BY "
                + esc(", ".join(agg["group_by"]) or "(없음 — 전체 1행)"))]
    for f in agg["functions"]:
        expr = upper_funcs(f["expr"])
        target = f" → {f['alias']}" if f["alias"] and f["alias"] not in expr else ""
        ko = "·".join(FUNC_KO.get(x, x) for x in f["functions"])
        rows.append(row(f"&nbsp;&nbsp;<span style='color:{AGG_COLOR}'>{esc(expr)}</span>"
                        f"{esc(target)} <span style='color:#888888'>({ko})</span>"))
    if agg.get("where"):
        rows.append(row("&nbsp;&nbsp;<span style='color:#888888'>집계 대상 조건:</span> "
                        + esc(agg["where"])))
    for j in agg.get("join_conditions", []):
        rows.append(row(f"&nbsp;&nbsp;<span style='color:#888888'>집계 대상 조건 "
                        f"(join {esc(j['target'])}):</span> " + esc(j["on"])))
    if agg.get("having"):
        rows.append(row("&nbsp;&nbsp;<span style='color:#888888'>HAVING:</span> "
                        + esc(agg["having"])))
    tag = f"<b style='color:{AGG_COLOR}'>▶ {{}}</b>&nbsp;&nbsp;"
    if AGG_NOTES.get(name):
        rows.append(row(tag.format("가져오는 값") + esc(AGG_NOTES[name])))
    usage = agg.get("usage") or {}
    for read in usage.get("reads", []):
        field = read["ref"].split(".")[-1]
        renamed = f" → {read['exposed_as']}" if read["exposed_as"] != field else ""
        rows.append(row(tag.format("읽는 곳") + esc(", ".join(read["views"]))
                        + f" <span style='color:#888888'>:</span> {esc(read['ref'] + renamed)}"))
    for cond in usage.get("conditions", []):
        rows.append(row(tag.format("조건에 사용") + esc(cond["view"])
                        + f" <span style='color:#888888'>:</span> {esc(cond['condition'])}"))
    return rows


def kind_text(n):
    if n.get("source_kind") == "projection":
        return ("root " if n.get("root") else "") + "projection view"
    return ("root " if n.get("root") else "") + n.get("kind", "view entity")


def header(kind, name, label):
    parts = [f"<span style='font-size:9px;color:#666666'>«{kind}»</span>",
             f"<b>{name}</b>"]
    if label:
        parts.append(f"<span style='font-size:10px'>{esc(label)}</span>")
    return "<br>".join(parts)


SPEC = {}
for name, n in N.items():
    cat = n["category"]
    agg = n.get("aggregation")
    if cat == "program":
        rows = []
        if name in OUT:
            rows.append(row(f"<b style='color:#b85450'>{OUT[name]['badge']}</b>&nbsp;&nbsp;"
                            f"{esc(OUT[name]['title'])}"))
        if name in OP_TABLES:
            tabs = "·".join(("탭" + t[3:]) if t.startswith("tab") else t
                            for t in OP_TABLES[name]["of"])
            rows.append(row(f"<b style='color:#b85450'>상세 화면 테이블</b>&nbsp;&nbsp;"
                            f"{esc(tabs)} ({OP_TABLES[name]['via']})"))
        if name in EXTRA:
            rows.append(row(f"<b style='color:#b85450'>앱에서 직접 조회</b>&nbsp;&nbsp;"
                            f"{esc(EXTRA[name].get('used_by', ''))}"))
        rows += [row(f"<b>PK</b>&nbsp;&nbsp;{k}") for k in n["keys"]] or \
                [row("<i style='color:#999999'>(key 없음)</i>")]
        if agg:
            rows += aggregation_rows(name, agg)
        if n.get("external_assoc_count"):
            rows.append(row(f"<i style='color:#666666'>+ 외부 연관 {n['external_assoc_count']}개 "
                            f"({EXT_PAGE}페이지 참조)</i>"))
        kind = kind_text(n)
        if agg:
            kind += " · 집계"
        if name in OUT:
            kind = f"{'화면 출력' if HAS_TABS else '최종 출력'} · {kind}"
        elif name in EXPOSED:
            kind = f"{kind} · 서비스 노출"
        SPEC[name] = {"type": "table", "rows": rows, "kind": kind,
                      "h": HEAD_H + sum(h for _, h in rows)}
    else:
        if cat == "external":
            kind = f"패키지 외부 view · {n.get('package', '')}"
            fill, stroke, dashed = "#f5f5f5", "#666666", 1
        elif cat == "standard":
            kind, fill, stroke, dashed = "SAP 표준 CDS view", "#e6edf5", "#5b7896", 0
        elif cat == "table":
            kind, fill, stroke, dashed = "DB table", "#ffe6cc", "#d79b00", 0
        else:
            kind, fill, stroke, dashed = "저장소에 없는 객체", "#f5f5f5", "#999999", 1
        label = n.get("label", "")
        if agg:
            kind += " · 집계"
            label = (label + " · " if label else "") + "Σ " + ", ".join(
                upper_funcs(f["expr"]) for f in agg["functions"])
        if name in EXTRA:
            label = (label + " · " if label else "") + EXTRA[name]["used_by"] + "에서 직접 조회"
        SPEC[name] = {"type": "simple", "kind": kind, "label": label,
                      "fill": fill, "stroke": stroke, "dashed": dashed,
                      "h": (78 if len(label) > 34 else 62) if label else 50}

# ── ranking: dependency depth, bases at 0, outputs alone at the end ──

deps = {n: [] for n in N}
users = {n: [] for n in N}
for e in EDGES:
    if e["dst"] not in deps[e["src"]]:
        deps[e["src"]].append(e["dst"])
        users[e["dst"]].append(e["src"])

rank, state, back = {}, {}, set()


def visit(n):
    if n in rank:
        return rank[n]
    if state.get(n) == 1:
        return None
    state[n] = 1
    r = 0
    for d in deps[n]:
        rd = visit(d)
        if rd is None:
            back.add((n, d))
            continue
        r = max(r, rd + 1)
    state[n] = 2
    rank[n] = r
    return r


for n in N:
    visit(n)
outputs = list(OUT)
movable = [o for o in outputs if not users[o]]
others = [n for n in N if n not in movable]
last = max([rank[n] for n in others] + [0]) + 1
for o in movable:
    rank[o] = last
R = max(rank.values())

# ── sections (lanes): from the documents for centre nodes, derived for the rest ─

sec_index = {}
for i, s in enumerate(SECTIONS):
    for ent in s["entities"]:
        sec_index[ent] = i
unassigned = [n for n in N if 0 < rank[n] < R and n not in sec_index]
if not HAS_SECTIONS:
    SECTIONS = [{"title": "", "note": "", "entities": unassigned}]     # one plain lane
elif unassigned:
    SECTIONS.append({"title": "기타", "note": "문서에서 기능을 확인하지 못한 view",
                     "entities": unassigned})
for n in unassigned:
    sec_index[n] = len(SECTIONS) - 1
LINEAGE = ("select", "projection", "union", "join")
lane = dict(sec_index)


def lane_from_sources(n, depth=0):
    for e in EDGES:
        if e["src"] == n and e["kind"] in ("projection", "select"):
            if e["dst"] in lane:
                return lane[e["dst"]]
            if depth < 10:
                found = lane_from_sources(e["dst"], depth + 1)
                if found is not None:
                    return found
    return None


for n in N:
    if n in lane:
        continue
    if rank[n] == 0:      # a source: follow whoever reads it
        votes = [lane[e["src"]] for e in EDGES
                 if e["dst"] == n and e["kind"] in LINEAGE and e["src"] in lane]
        if not votes:
            votes = [lane[u] for u in users[n] if u in lane]
        lane[n] = max(sorted(set(votes)), key=votes.count) if votes else len(SECTIONS) - 1
for n in N:
    if n not in lane:     # outputs and anything else: follow the main data source
        found = lane_from_sources(n)
        lane[n] = found if found is not None else len(SECTIONS) - 1

# ── long edges get pass-through nodes so they can run between boxes ──

paths, segs, dummy_rank, reversed_edges = [], [], {}, set()
for i, e in enumerate(EDGES):
    s, d = e["src"], e["dst"]
    if rank[s] == rank[d]:
        paths.append(None)          # same column: drawn as a direct line
        continue
    # chain always runs from the right-hand node to the left-hand node
    hi, lo = (s, d) if rank[s] > rank[d] else (d, s)
    if hi == d:
        reversed_edges.add(i)       # cycle: the reader sits left of what it reads
    chain = [hi]
    for r in range(rank[hi] - 1, rank[lo], -1):
        dn = f"~{i}:{r}"
        dummy_rank[dn] = r
        lane[dn] = lane[lo]
        chain.append(dn)
    chain.append(lo)
    paths.append(chain)
    for a, b in zip(chain, chain[1:]):
        segs.append((a, b, i))


def is_dummy(n):
    return n.startswith("~")


def rk(n):
    return dummy_rank[n] if is_dummy(n) else rank[n]


H = {n: SPEC[n]["h"] for n in N}
H.update({d: 16 for d in dummy_rank})
left_nb, right_nb = {}, {}
for a, b, _ in segs:
    left_nb.setdefault(a, []).append(b)
    right_nb.setdefault(b, []).append(a)

# ── ordering inside columns: by section first, then barycenter ───────

layers = [[] for _ in range(R + 1)]
placed = set()


def dfs(n):
    if n in placed:
        return
    placed.add(n)
    layers[rank[n]].append(n)
    for d in deps[n]:
        dfs(d)


for o in outputs:
    dfs(o)
for n in sorted(N, key=lambda x: (-rank[x], x)):
    dfs(n)
for dn, r in dummy_rank.items():
    layers[r].append(dn)
for layer in layers:
    layer.sort(key=lambda n: lane[n])          # stable: keeps the DFS order inside a lane
layers[R].sort(key=lambda n: (lane[n], OUT_ORDER.get(n, 99)))


def crossings(ls):
    pos = {n: i for layer in ls for i, n in enumerate(layer)}
    by_gutter = {}
    for a, b, _ in segs:
        by_gutter.setdefault(rk(b), []).append((pos[b], pos[a]))
    total = 0
    for pairs in by_gutter.values():
        pairs.sort()
        for x in range(len(pairs)):
            for y in range(x + 1, len(pairs)):
                if pairs[x][0] != pairs[y][0] and pairs[x][1] > pairs[y][1]:
                    total += 1
    return total


def reorder(layer, nbrs, ref):
    pos = {n: i for i, n in enumerate(ref)}
    cur = {n: i for i, n in enumerate(layer)}

    def key(n):
        ns = [pos[m] for m in nbrs.get(n, []) if m in pos]
        bary = (sum(ns) / len(ns)) if ns else cur[n] * len(ref) / max(len(layer), 1)
        return lane[n], bary

    layer.sort(key=key)


best = (crossings(layers), [list(l) for l in layers])
for it in range(30):
    if it % 2 == 0:
        for r in range(R - 1, -1, -1):
            reorder(layers[r], right_nb, layers[r + 1])
    else:
        for r in range(1, R):                   # the output column keeps tab order
            reorder(layers[r], left_nb, layers[r - 1])
    c = crossings(layers)
    if c < best[0]:
        best = (c, [list(l) for l in layers])
layers = best[1]

# ── coordinates ──────────────────────────────────────────────────────

BAND_TOP, TOP, LEFT = 84, 128, 60
PAD_TOP, PAD_BOTTOM, LANE_GAP = (52 if HAS_SECTIONS else 16), 22, 18


def gap(a, b):
    if is_dummy(a) and is_dummy(b):
        return 10
    return 24 if (is_dummy(a) or is_dummy(b)) else 40


groups = {}
for r, layer in enumerate(layers):
    for n in layer:
        groups.setdefault((r, lane[n]), []).append(n)
need = [60] * len(SECTIONS)
for (r, L), nodes in groups.items():
    stack = sum(H[n] for n in nodes) + sum(gap(a, b) for a, b in zip(nodes, nodes[1:]))
    need[L] = max(need[L], stack)
lane_top, lane_bottom = [], []
cur = TOP
for L in range(len(SECTIONS)):
    lane_top.append(cur)
    cur += PAD_TOP + need[L] + PAD_BOTTOM
    lane_bottom.append(cur)
    cur += LANE_GAP

Y = {}
for (r, L), nodes in groups.items():
    y = lane_top[L] + PAD_TOP
    for i, n in enumerate(nodes):
        if i:
            y += gap(nodes[i - 1], n)
        Y[n] = y
        y += H[n]


def center(n):
    return Y[n] + H[n] / 2


def relax(r, nbrs):
    for L in range(len(SECTIONS)):
        nodes = groups.get((r, L), [])
        lo0, hi0 = lane_top[L] + PAD_TOP, lane_bottom[L] - PAD_BOTTOM
        prev = None
        for n in nodes:
            ns = nbrs.get(n, [])
            want = (sum(center(m) for m in ns) / len(ns) - H[n] / 2) if ns else Y[n]
            lo = lo0 if prev is None else Y[prev] + H[prev] + gap(prev, n)
            Y[n] = max(want, lo)
            prev = n
        nxt = None
        for n in reversed(nodes):
            hi = (hi0 - H[n]) if nxt is None else Y[nxt] - gap(n, nxt) - H[n]
            Y[n] = min(Y[n], hi)
            nxt = n


for (r, L) in list(groups):
    groups[(r, L)] = [n for n in layers[r] if lane[n] == L]     # final order
for _ in range(10):
    for r in range(1, R + 1):
        relax(r, left_nb)
    for r in range(R - 1, -1, -1):          # finish with sources following their readers
        relax(r, right_nb)
for n in Y:
    Y[n] = round(Y[n])

port, side_items = {}, {}
for a, b, i in segs:
    if not is_dummy(a):
        side_items.setdefault((a, "L"), []).append((center(b), i, b))
    if not is_dummy(b):
        side_items.setdefault((b, "R"), []).append((center(a), i, a))
for (n, side), items in side_items.items():
    items.sort()
    for k, (_, i, other) in enumerate(items):
        port[(n, side, i, other)] = round((k + 1) / (len(items) + 1), 3)


def end_y(n, side, i, other):
    if is_dummy(n):
        return center(n)
    return Y[n] + H[n] * port[(n, side, i, other)]


bent = {}
for a, b, i in segs:
    ya, yb = end_y(a, "L", i, b), end_y(b, "R", i, a)
    if abs(ya - yb) >= 1:
        bent.setdefault(rk(b), []).append(((ya + yb) / 2, ya, yb, a, b, i))
gutter_w = [max(110, 18 * (len(bent.get(g, [])) + 1)) for g in range(R)]
X = [LEFT]
for g in range(R):
    X.append(X[-1] + BOX_W + gutter_w[g])
lane_x = {}
for g, items in bent.items():
    items.sort()
    for k, (_, ya, yb, a, b, i) in enumerate(items):
        lane_x[(a, b, i)] = round(X[g] + BOX_W + gutter_w[g] * (k + 1) / (len(items) + 1), 1)

bottom = lane_bottom[-1] + 28
right_edge = X[R] + BOX_W

# ══ Page 1 ═══════════════════════════════════════════════════════════

title = (f"{SCREEN.get('program') or M['program_package'].upper()} "
         f"{SCREEN.get('app_title') or ''}").strip() + " — CDS ERD"
cmds.append("project new --name " + q("View 관계") + " --format a3 --landscape")
cmds.append("layer rename 1 " + q("배경"))
cmds.append("layer add " + q("엔터티") + " --id ent")

src_x1 = X[0] + BOX_W + gutter_w[0] / 2
out_x0 = X[R - 1] + BOX_W + gutter_w[R - 1] / 2
bands = [
    ("band_src", "Source (SAP 표준 CDS view · DB table)", "#e9f0f7", "#b7c9dc",
     X[0] - 30, src_x1),
    ("band_cds", "CDS View", "#f1f7ea", "#c3d6ae", src_x1, out_x0),
    ("band_out", "최종 출력 (화면 탭)" if HAS_TABS else "최종 출력",
     "#fdeceb", "#e3b3ae", out_x0, right_edge + 30),
]
for bid, text, fill, stroke, x0, x1 in bands:
    if x1 - x0 < 1:
        continue                      # no middle column: nothing between source and output
    style = (f"rounded=0;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
             "verticalAlign=top;align=center;fontSize=16;fontStyle=1;fontColor=#444444;"
             "spacingTop=8;")
    cmds.append(f"shape add --id {bid} --layer 1 --x {round(x0)} --y {BAND_TOP} "
                f"-w {round(x1 - x0)} --height {round(bottom - BAND_TOP)} "
                f"-l {q(text)} --style {q(style)}")

# functional sections inside the CDS View band
SEC_STYLE = ("rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=#ffffff;fillOpacity=55;"
             "strokeColor=#8fae78;dashed=1;verticalAlign=top;align=left;spacingLeft=14;"
             "spacingTop=8;fontSize=14;fontColor=#35521f;")
for L, section in enumerate(SECTIONS if HAS_SECTIONS else []):
    text = f"<b>{esc(section['title'])}</b>"
    if section.get("note"):
        text += (f"&nbsp;&nbsp;&nbsp;<span style='font-size:11px;color:#555555'>"
                 f"{esc(section['note'])}</span>")
    cmds.append(f"shape add --id sec{L + 1} --layer 1 --x {round(src_x1 + 14)} "
                f"--y {lane_top[L]} -w {round(out_x0 - src_x1 - 28)} "
                f"--height {lane_bottom[L] - lane_top[L]} --html -l {q(text)} "
                f"--style {q(SEC_STYLE)}")
cmds.append("shape add -t title --id title --layer 1 --x 30 --y 22 -w 900 --height 40 -l "
            + q(title + "  (좌: Source → 우: 최종 출력)") + " --set align=left")
cmds.append("layer lock 1")

for r, layer in enumerate(layers):
    for name in layer:
        if is_dummy(name):
            continue
        n, spec = N[name], SPEC[name]
        x, y = X[r], Y[name]
        if spec["type"] == "table":
            parts = name.split("_")       # Z<module>_<R|I|C|P>_<name> naming
            letter = parts[1] if len(parts) > 2 and len(parts[1]) == 1 else "I"
            fill, stroke = COLORS.get(letter, COLORS["I"])
            width = 3 if name in OUT else 1
            style = ("swimlane;html=1;whiteSpace=wrap;fontStyle=0;"
                     f"startSize={HEAD_H};horizontal=1;collapsible=0;"
                     "childLayout=stackLayout;horizontalStack=0;resizeParent=1;"
                     f"resizeLast=0;fillColor={fill};strokeColor={stroke};"
                     f"strokeWidth={width};swimlaneFillColor=#ffffff;rounded=1;arcSize=8;")
            cmds.append(f"shape add --id {cid(name)} --layer ent --x {x} --y {y} "
                        f"-w {BOX_W} --height {spec['h']} --html "
                        f"-l {q(header(spec['kind'], name, n.get('label', '')))} "
                        f"--style {q(style)}")
            offset = HEAD_H
            for k, (text, height) in enumerate(spec["rows"]):
                cmds.append(f"shape add --parent {cid(name)} --id {cid(name)}.r{k + 1} "
                            f"--x 0 --y {offset} -w {BOX_W} --height {height} "
                            f"--html -l {q(text)} --style {q(ROW_STYLE)}")
                offset += height
        else:
            style = (f"rounded=1;whiteSpace=wrap;html=1;dashed={spec['dashed']};"
                     f"fillColor={spec['fill']};strokeColor={spec['stroke']};"
                     "fontColor=#333333;")
            cmds.append(f"shape add --id {cid(name)} --layer ent --x {x} --y {y} "
                        f"-w {BOX_W} --height {spec['h']} --html "
                        f"-l {q(header(spec['kind'], name, spec['label']))} "
                        f"--style {q(style)}")

ER = {"[1]": "ERmandOne", "[0..1]": "ERzeroToOne", "[0..*]": "ERzeroToMany",
      "[*]": "ERzeroToMany", "[1..*]": "ERoneToMany", "": "ERzeroToOne"}
BASE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;jettySize=auto;orthogonalLoop=1;"
KIND_STYLE = {
    "assoc": ("endArrow={er};endFill=0;endSize=10;startArrow=none;strokeColor=#1f4e79;"
              "strokeWidth=1.3;fontSize=10;fontColor=#1f4e79;labelBackgroundColor=#ffffff;"),
    "composition": ("startArrow=diamondThin;startFill=1;startSize=14;endArrow={er};"
                    "endFill=0;endSize=10;strokeColor=#1f4e79;strokeWidth=1.3;fontSize=10;"
                    "fontColor=#1f4e79;labelBackgroundColor=#ffffff;"),
    "select": ("dashed=1;endArrow=block;endFill=0;endSize=9;strokeColor=#7f7f7f;"
               "fontSize=9;fontColor=#7f7f7f;labelBackgroundColor=#ffffff;"),
    "union": ("dashed=1;endArrow=block;endFill=0;endSize=9;strokeColor=#7f7f7f;"
              "fontSize=9;fontColor=#7f7f7f;labelBackgroundColor=#ffffff;"),
    "projection": ("dashed=1;endArrow=block;endFill=0;endSize=9;strokeColor=#9673a6;"
                   "fontSize=9;fontColor=#9673a6;labelBackgroundColor=#ffffff;"),
    "join": ("dashed=1;dashPattern=1 3;endArrow=none;strokeColor=#b85450;strokeWidth=1.5;"
             "fontSize=9;fontColor=#b85450;labelBackgroundColor=#ffffff;"),
}
for i, e in enumerate(EDGES):
    style = BASE + KIND_STYLE[e["kind"]].format(er=ER.get(e.get("card", ""), "ERzeroToOne"))
    line = f"connect add {cid(e['src'])} {cid(e['dst'])} --id v{i + 1}"
    chain = paths[i]
    points = []
    if chain:
        right_port = port[(chain[0], "L", i, chain[1])]      # on the right-hand node
        left_port = port[(chain[-1], "R", i, chain[-2])]     # on the left-hand node
        for a, b in zip(chain, chain[1:]):
            if (a, b, i) in lane_x:
                x = lane_x[(a, b, i)]
                points.append((x, round(end_y(a, "L", i, b), 1)))
                points.append((x, round(end_y(b, "R", i, a), 1)))
        if i in reversed_edges:      # connector starts at the left-hand node
            style += (f"exitX=1;exitY={left_port};exitDx=0;exitDy=0;"
                      f"entryX=0;entryY={right_port};entryDx=0;entryDy=0;")
            points.reverse()
        else:
            style += (f"exitX=0;exitY={right_port};exitDx=0;exitDy=0;"
                      f"entryX=1;entryY={left_port};entryDx=0;entryDy=0;")
    line += f" --raw-style {q(style)}"
    if e.get("label"):
        line += f" -l {q(e['label'])}"
    for x, y in points:
        line += f" --waypoint {x},{y}"
    cmds.append(line)

legend = (
    "<b>범례</b>&nbsp;&nbsp;"
    "<span style='background-color:#dae8fc;border:1px solid #6c8ebf'>&nbsp;&nbsp;&nbsp;&nbsp;</span> R: Root/BO&nbsp;&nbsp;"
    "<span style='background-color:#d5e8d4;border:1px solid #82b366'>&nbsp;&nbsp;&nbsp;&nbsp;</span> I: Interface&nbsp;&nbsp;"
    "<span style='background-color:#fff2cc;border:1px solid #d6b656'>&nbsp;&nbsp;&nbsp;&nbsp;</span> C: Consumption&nbsp;&nbsp;"
    "<span style='background-color:#e1d5e7;border:1px solid #9673a6'>&nbsp;&nbsp;&nbsp;&nbsp;</span> P: Projection&nbsp;&nbsp;"
    "<span style='background-color:#f5f5f5;border:1px dashed #666666'>&nbsp;&nbsp;&nbsp;&nbsp;</span> 패키지 외부 view&nbsp;&nbsp;"
    "<span style='background-color:#e6edf5;border:1px solid #5b7896'>&nbsp;&nbsp;&nbsp;&nbsp;</span> SAP 표준 view&nbsp;&nbsp;"
    "<span style='background-color:#ffe6cc;border:1px solid #d79b00'>&nbsp;&nbsp;&nbsp;&nbsp;</span> DB table&nbsp;&nbsp;"
    "<b>굵은 테두리</b> = 최종 출력 엔터티&nbsp;&nbsp;"
    f"<b style='color:{AGG_COLOR}'>Σ 집계</b> = 집계 함수 · GROUP BY · 집계 대상 조건<br>"
    "<span style='color:#7f7f7f'>┄┄▷ 회색 점선</span>: select from (화살표가 데이터 소스를 가리킴)&nbsp;&nbsp;"
    "<span style='color:#9673a6'>┄┄▷ 보라 점선</span>: projection on&nbsp;&nbsp;"
    "<span style='color:#b85450'>┈┈ 붉은 점선</span>: join&nbsp;&nbsp;"
    "<span style='color:#1f4e79'>━━ 파란 실선</span>: association (끝 모양 = 대상 쪽 카디널리티, 라벨 = 별칭)&nbsp;&nbsp;"
    "<span style='color:#1f4e79'>◆━ 파란 실선</span>: composition (◆ = 부모)"
)
cmds.append(f"shape add -t text --id legend --layer ent --x 960 --y 14 "
            f"-w {max(1400, round(right_edge - 930))} --height 56 --html "
            f"-l {q(legend)} --style "
            + q("text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;"
                "strokeColor=#cccccc;fillColor=#ffffff;spacingLeft=10;fontSize=11;rounded=1;"))

# ══ Next page: abstract entities (only when the package has any) ═════

page_no = 0
A_W, A_H, A_PITCH = 330, 56, 76
A_COL = [40, 570, 1100, 1630]
HALF_DX = 1720
composed = {c["target"] for e in A.values() for c in e["compositions"]}
roots = [n for n in A if n not in composed]
a_fill, a_stroke = COLORS["A"]
a_edges = []
if A:
    page_no += 1
    cmds.append("page add --name " + q("Abstract 엔터티"))
    cmds.append(f"page use {page_no}")
    cmds.append("shape add -t title --id title --x 40 --y 30 -w 1100 --height 40 -l "
                + q("Abstract 엔터티 (Action 파라미터 · 결과 구조)") + " --set align=left")


def place(name, depth, y, dx):
    e = A[name]
    start = y
    children = [c["target"] for c in e["compositions"] if c["target"] in A]
    for comp in e["compositions"]:
        if comp["target"] in A:
            a_edges.append((name, comp["target"], comp["alias"], comp["card"]))
    next_y = y
    for child in children:
        next_y = place(child, depth + 1, next_y, dx)
    end = max(next_y, y + A_PITCH)
    box_y = start if not children else start + (end - start - A_PITCH) / 2
    style = (f"rounded=1;whiteSpace=wrap;html=1;"
             f"fillColor={a_fill if e['root'] else '#ffffff'};strokeColor={a_stroke};")
    kind = ("root " if e["root"] else "") + e["kind"]
    cmds.append(f"shape add --id {cid(name)} --x {A_COL[min(depth, 3)] + dx} --y {round(box_y)} "
                f"-w {A_W} --height {A_H} --html "
                f"-l {q(header(kind, name, e['label']))} --style {q(style)}")
    return end


half = (len(roots) + 1) // 2
for h, group in enumerate((roots[:half], roots[half:])):
    y = 110
    for root in group:
        y = place(root, 0, y, h * HALF_DX) + 34
for k, (parent, child, alias, card) in enumerate(a_edges):
    style = (BASE + "startArrow=diamondThin;startFill=1;startSize=14;"
             f"endArrow={ER.get(card, 'ERzeroToMany')};endFill=0;endSize=10;"
             "strokeColor=#b85450;fontSize=10;fontColor=#7a2e2b;"
             "labelBackgroundColor=#ffffff;exitX=1;exitY=0.5;exitDx=0;exitDy=0;"
             "entryX=0;entryY=0.5;entryDx=0;entryDy=0;")
    cmds.append(f"connect add {cid(parent)} {cid(child)} --id c{k + 1} "
                f"--raw-style {q(style)} -l {q(alias + ' ' + card)}")
if A:
    cmds.append("shape add -t text --id legend --x 1760 --y 24 -w 900 --height 50 --html -l "
                + q("<b>범례</b>&nbsp;&nbsp;◆━ composition (◆ = 부모, 끝 모양 = 자식 카디널리티, "
                    "라벨 = 별칭 [카디널리티])&nbsp;&nbsp;"
                    "<span style='background-color:#f8cecc;border:1px solid #b85450'>&nbsp;&nbsp;&nbsp;&nbsp;</span> root")
                + " --style "
                + q("text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;"
                    "strokeColor=#cccccc;fillColor=#ffffff;spacingLeft=10;fontSize=11;rounded=1;"))

# ══ Last page: associations that leave the model ═════════════════════

n_rows = len(M["external_associations"])
if n_rows:
    page_no += 1
    cmds.append("page add --name " + q("외부 연관"))
    cmds.append(f"page use {page_no}")
    cmds.append("shape add -t title --id title --x 40 --y 30 -w 1100 --height 40 -l "
                + q("View → 패키지 외부 연관 (Value Help · 텍스트 · 표준 view)")
                + " --set align=left")
    TD = "style='border:1px solid #999999;padding:3px 8px'"
    TH = "style='border:1px solid #999999;padding:3px 8px;background-color:#eeeeee'"
    rows_html = [f"<tr><th {TH}>View</th><th {TH}>별칭</th><th {TH}>대상</th>"
                 f"<th {TH}>카디널리티</th></tr>"]
    for a in M["external_associations"]:
        rows_html.append(f"<tr><td {TD}>{a['view']}</td><td {TD}>{a['alias']}</td>"
                         f"<td {TD}>{a['target']}</td><td {TD}>{a['card']}</td></tr>")
    table = ("<table style='border-collapse:collapse;font-size:11px;text-align:left'>"
             + "".join(rows_html) + "</table>")
    cmds.append(f"shape add --id ext_table --x 40 --y 90 -w 1000 "
                f"--height {26 * (n_rows + 1) + 20} --html -l {q(table)} --style "
                + q("rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
                    "strokeColor=none;fillColor=none;spacingLeft=4;spacingTop=4;"))

cmds.append("page use 0")
cmds.append(f"save -o {q(out_path)} --overwrite")
cmds.append("quit")

script = "\n".join(cmds) + "\n"
for target in (out_path, script_path):
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
with open(script_path, "w", encoding="utf-8", newline="\n") as f:
    f.write(script)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
result = subprocess.run([cli_exe], input=script, capture_output=True, text=True,
                        encoding="utf-8")
errors = [l for l in (result.stdout + "\n" + result.stderr).splitlines()
          if "Error" in l or "✗" in l]
print(f"commands: {len(cmds)}  nodes: {len(N)}  edges: {len(EDGES)}  "
      f"columns: {R + 1}  crossings: {best[0]}  back edges: {len(back)}  pages: {page_no + 1}")
print("final outputs:", ", ".join(f"{n} ({OUT[n]['badge']})" for n in OUT))
for t in SCREEN.get("tabs", []):
    if t["entity"] not in N:
        print(f"WARNING: 탭 엔터티 {t['entity']}가 모델에 없어 최종 출력에서 빠졌다. "
              "screen.json과 백엔드 패키지를 확인한다.")
for s, d in sorted(back):
    print(f"  NOTE: 순환 관계 {s} -> {d} (서로를 참조한다. 읽는 쪽이 더 왼쪽 열에 놓였다)")
for i, e in enumerate(EDGES):
    if paths[i] is None:
        print(f"  NOTE: {e['src']} -> {e['dst']} 는 같은 열이라 직선으로 그렸다. "
              "상자를 가로지르는지 그림에서 확인한다.")
for L, section in enumerate(SECTIONS):
    members = [n for n in N if lane[n] == L]
    print(f"  section {L + 1} {section['title'] or '(구분 없음)'}: " + ", ".join(
        f"{n}@{rank[n]}" for n in sorted(members, key=lambda x: rank[x])))
if HAS_SECTIONS and SECTIONS[-1]["title"] == "기타":
    print("  NOTE: '기타' 구역의 view는 sections.json에 없는 것이다. 맞는 구역에 넣는다.")
aggregated = [n for n in N if N[n].get("aggregation")]
print("aggregating views:", ", ".join(aggregated) or "(none)")
print(f"exit: {result.returncode}  error lines: {len(errors)}")
for line in errors[:15]:
    print("  ", line[:300])
