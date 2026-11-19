"""Build a program-level CDS model from an abapGit repo.

Usage:
    python cds_model.py <repo src dir> <program package dir> <screen.json> <out model.json>

1. Indexes every *.ddls.asddls in the repo (entity name -> source) and every
   *.tabl.xml (DB tables).
2. Takes the view entities of the program package, plus any entity the
   frontend reads directly (screen.json "extra_entities").
3. Follows data lineage (select from / projection on / join / union) out of
   the package, recursively, until it reaches something that is not a CDS
   source in the repo: a DB table or an SAP standard view. Those are the
   bases.
4. Keeps associations only between entities that ended up in the model;
   associations leaving it are listed separately.

Extraction is regex based (no CDS compiler): good for structure, not a
guarantee for unusual syntax.
"""
import glob
import json
import os
import re
import sys

repo_src, package_dir, screen_path, out_path = sys.argv[1:5]
screen = json.load(open(screen_path, encoding="utf-8"))


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(//|--).*", "", text)


DEFINE = re.compile(
    r"define\s+(root\s+)?(view\s+entity|abstract\s+entity|custom\s+entity|"
    r"table\s+function|view)\s+(\w+)", re.I)
JOIN = re.compile(
    r"\b((?:inner|left\s+outer|right\s+outer|left|right|cross)\s+)?"
    r"(?:(?:to\s+)?(?:exact\s+)?(?:one|many)\s+(?:to\s+(?:exact\s+)?(?:one|many)\s+)?)?"
    r"join\s+(\w+)", re.I)


AGG = re.compile(r"\b(sum|max|min|avg|count)\s*\(", re.I)
ANNOTATION = re.compile(
    r"@[\w.]+\s*(?::\s*(?:'[^']*'|#\w+|\[[^\]]*\]|\{[^}]*\}|[\w.]+))?")


WHERE = re.compile(r"\bwhere\b(.*?)(?=\bgroup\s+by\b|\bhaving\b|\bunion\b|$)", re.I | re.S)
TEXTS = {}      # ENTITY NAME -> comment-stripped source (not written to the model)


def _collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


def where_clause(text, search_from=0):
    """The WHERE condition of a view's (first) select, or an empty string."""
    start = text.find("{", search_from)
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                found = WHERE.search(text[i + 1:])
                return _collapse(found.group(1)) if found else ""
    return ""


def aggregation(text, search_from=0):
    """Aggregate functions, GROUP BY, WHERE and HAVING of a view (or None).

    ``search_from`` must point past the ``define ...`` clause: header
    annotations such as ``@ObjectModel.usageType:{...}`` also contain braces
    and would otherwise be mistaken for the element list.
    """
    start = text.find("{", search_from)
    if start < 0:
        return None
    depth, end = 0, None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return None
    elements, current, level = [], [], 0
    for ch in text[start + 1:end]:
        if ch in "([{":
            level += 1
        elif ch in ")]}":
            level -= 1
        if ch == "," and level == 0:
            elements.append("".join(current))
            current = []
        else:
            current.append(ch)
    elements.append("".join(current))
    functions = []
    for element in elements:
        clean = _collapse(ANNOTATION.sub("", element))
        if not AGG.search(clean):
            continue
        alias = re.search(r"\bas\s+(\w+)\s*$", clean, re.I)
        expr = clean[:alias.start()].strip() if alias else clean
        expr = re.sub(r"^key\s+", "", expr, flags=re.I)
        expr = re.sub(r"\s*\(\s*", "(", expr)
        expr = re.sub(r"\s*\)", ")", expr)
        functions.append({
            "expr": expr, "alias": alias.group(1) if alias else "",
            "functions": sorted({m.group(1).upper() for m in AGG.finditer(expr)}),
        })
    tail = text[end + 1:]
    where = WHERE.search(tail)
    group = re.search(r"\bgroup\s+by\b(.*?)(?=\bhaving\b|\bunion\b|$)", tail, re.I | re.S)
    having = re.search(r"\bhaving\b(.*?)(?=\bunion\b|$)", tail, re.I | re.S)
    group_by = ([re.sub(r"^.*\.", "", f.strip()) for f in group.group(1).split(",")
                 if f.strip()] if group else [])
    if not functions and not group_by:
        return None
    # A filter on what gets aggregated can also sit in a join's ON clause.
    join_conditions = []
    for j in re.finditer(
            r"\bjoin\s+(\w+)(?:\s+as\s+\w+)?\s+on\b(.*?)"
            r"(?=\b(?:inner|left|right|cross)\s|\bjoin\b|\bassociation\b|\bcomposition\b|$)",
            text[search_from:start], re.I | re.S):
        join_conditions.append({"target": j.group(1), "on": _collapse(j.group(2))})
    return {"functions": functions, "group_by": group_by,
            "where": _collapse(where.group(1)) if where else "",
            "having": _collapse(having.group(1)) if having else "",
            "join_conditions": join_conditions}


def parse(path):
    raw = open(path, encoding="utf-8-sig", errors="replace").read()
    text = strip_comments(raw)
    m = DEFINE.search(text)
    if not m:
        return None
    label = re.search(r"@EndUserText\.label\s*:\s*'([^']*)'", raw)
    ent = {
        "name": m.group(3), "root": bool(m.group(1)),
        "kind": re.sub(r"\s+", " ", m.group(2).lower()),
        "label": label.group(1) if label else "",
        "package": os.path.basename(os.path.dirname(path)),
        "lineage": [], "associations": [], "compositions": [],
        "parent": None, "keys": [],
        "aggregation": aggregation(text, m.end()),
        "where": where_clause(text, m.end()),
    }
    TEXTS[ent["name"].upper()] = text
    s = re.search(r"as\s+projection\s+on\s+(\w+)", text, re.I)
    if s:
        ent["lineage"].append({"type": "projection", "target": s.group(1)})
    else:
        s = re.search(r"as\s+select\s+(?:distinct\s+)?from\s+(\w+)", text, re.I)
        if s:
            ent["lineage"].append({"type": "select", "target": s.group(1)})
    for u in re.finditer(
            r"union\s+(?:all\s+)?select\s+(?:distinct\s+)?from\s+(\w+)", text, re.I):
        ent["lineage"].append({"type": "union", "target": u.group(1)})
    for j in JOIN.finditer(text):
        jtype = re.sub(r"\s+", " ", (j.group(1) or "inner").strip().lower())
        ent["lineage"].append({"type": "join", "join_type": jtype,
                               "target": j.group(2)})
    seen, unique = set(), []
    for item in ent["lineage"]:
        key = (item["type"], item["target"].upper())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    ent["lineage"] = unique

    for a in re.finditer(
            r"\bassociation\s*(\[[^\]]*\]|of\s+[\w\s]+?\s+to\s+(?:exact\s+one|one|many))?"
            r"\s*to\s+(parent\s+)?(\w+)\s+as\s+(\w+)", text, re.I):
        card = re.sub(r"\s+", " ", (a.group(1) or "").strip())
        if a.group(2):
            ent["parent"] = {"target": a.group(3), "alias": a.group(4)}
        else:
            ent["associations"].append(
                {"card": card, "target": a.group(3), "alias": a.group(4)})
    for c in re.finditer(
            r"\bcomposition\s*(\[[^\]]*\])?\s*of\s+(\w+)\s+as\s+(\w+)", text, re.I):
        ent["compositions"].append({"card": (c.group(1) or "").strip(),
                                    "target": c.group(2), "alias": c.group(3)})
    for c in re.finditer(
            r"(\w+)\s*:\s*composition\s*(\[[^\]]*\])?\s*of\s+(\w+)", text, re.I):
        ent["compositions"].append({"card": (c.group(2) or "").strip(),
                                    "target": c.group(3), "alias": c.group(1)})
    p = re.search(r"(\w+)\s*:\s*association\s+to\s+parent\s+(\w+)", text, re.I)
    if p:
        ent["parent"] = {"target": p.group(2), "alias": p.group(1)}
    # projection views: "_Item : redirected to composition child ZX_P_ITEM"
    for r in re.finditer(
            r"(\w+)\s*:\s*redirected\s+to\s+(composition\s+child\s+|parent\s+)?(\w+)",
            text, re.I):
        how = re.sub(r"\s+", " ", (r.group(2) or "").strip().lower())
        if how == "composition child":
            ent["compositions"].append({"card": "[0..*]", "target": r.group(3),
                                        "alias": r.group(1)})
        elif how == "parent":
            ent["parent"] = {"target": r.group(3), "alias": r.group(1)}
        else:
            ent["associations"].append({"card": "", "target": r.group(3),
                                        "alias": r.group(1)})

    # keys: only the first element list (a union repeats them in every branch)
    start = text.find("{", m.end())
    depth, end = 0, len(text)
    for i in range(max(start, 0), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = text[start:end] if start >= 0 else ""
    for line in body.splitlines():
        k = re.match(r"\s*key\s+(.+?)[;,]?\s*$", line, re.I)
        if k:
            expr = k.group(1)
            alias = re.search(r"\bas\s+(\w+)\s*$", expr, re.I)
            if alias:
                key_name = alias.group(1)
            else:
                field = re.match(r"(?:[\w.]+\.)?(\w+)\s*(?::.*)?$", expr)
                key_name = field.group(1) if field else expr
            if key_name not in ent["keys"]:
                ent["keys"].append(key_name)
    return ent


# 1. index the repo ---------------------------------------------------
index = {}
for path in glob.glob(os.path.join(repo_src, "**", "*.ddls.asddls"), recursive=True):
    name = os.path.basename(path).split(".")[0].upper()
    index[name] = path
tables = {os.path.basename(p).split(".")[0].upper()
          for p in glob.glob(os.path.join(repo_src, "**", "*.tabl.xml"), recursive=True)}

cache = {}


def entity(name):
    key = name.upper()
    if key not in cache:
        cache[key] = parse(index[key]) if key in index else None
    return cache[key]


def classify_base(name):
    key = name.upper()
    if key in tables:
        return "table"
    if key[0] in "ZY":
        return "unknown"       # customer object that is not in the repo
    if re.match(r"^[A-Z]_", key):
        return "standard"      # I_/C_/P_/A_... released SAP view
    return "table"             # SAP DDIC table (BKPF, ACDOCA, ...)


# 2. program entities -------------------------------------------------
program_package = os.path.basename(os.path.normpath(package_dir))
program, abstract = [], []
for path in sorted(glob.glob(os.path.join(package_dir, "*.ddls.asddls"))):
    ent = entity(os.path.basename(path).split(".")[0])
    if ent is None:
        continue
    (abstract if ent["kind"] == "abstract entity" else program).append(ent["name"])

nodes = {}


def add_node(name, category, ent=None):
    key = name.upper()
    if key in nodes:
        return nodes[key]
    node = {"name": ent["name"] if ent else name, "category": category}
    if ent:
        node.update({k: ent[k] for k in ("root", "kind", "label", "package", "keys",
                                         "aggregation")})
        node["source_kind"] = next(
            (l["type"] for l in ent["lineage"] if l["type"] in ("select", "projection")),
            None)
    nodes[key] = node
    return node


# 3. lineage closure --------------------------------------------------
edges = []
queue = []
for name in program:
    add_node(name, "program", entity(name))
    queue.append(name)
for extra in screen.get("extra_entities", []):
    ent = entity(extra["entity"])
    if ent is not None and extra["entity"].upper() not in nodes:
        add_node(extra["entity"], "external", ent)
        queue.append(extra["entity"])

while queue:
    name = queue.pop(0)
    ent = entity(name)
    for item in ent["lineage"]:
        target = item["target"]
        target_ent = entity(target)
        if target.upper() not in nodes:
            if target_ent is not None:
                add_node(target, "external", target_ent)
                queue.append(target)
            else:
                add_node(target, classify_base(target))
        edges.append({"kind": item["type"], "src": nodes[name.upper()]["name"],
                      "dst": nodes[target.upper()]["name"],
                      "label": (item.get("join_type", "") + " join").strip()
                      if item["type"] == "join" else
                      ("projection" if item["type"] == "projection" else
                       ("union" if item["type"] == "union" else ""))})

# 4. associations -----------------------------------------------------
external_assocs = []
for name in program:
    ent = entity(name)
    for a in ent["associations"]:
        if a["target"].upper() in nodes:
            edges.append({"kind": "assoc", "src": ent["name"],
                          "dst": nodes[a["target"].upper()]["name"],
                          "label": a["alias"], "card": a["card"]})
        else:
            external_assocs.append({"view": ent["name"], "alias": a["alias"],
                                    "target": a["target"], "card": a["card"]})
    nodes[name.upper()]["external_assoc_count"] = sum(
        1 for a in ent["associations"] if a["target"].upper() not in nodes)
    for c in ent["compositions"]:          # BO root -> child
        if c["target"].upper() in nodes:
            edges.append({"kind": "composition", "src": ent["name"],
                          "dst": nodes[c["target"].upper()]["name"],
                          "label": c["alias"], "card": c["card"] or "[0..*]"})

# screen entities must exist in the model, otherwise the ERD has no real output column
missing = [t["entity"] for t in screen.get("tabs", []) + screen.get("outputs", [])
           if t["entity"].upper() not in nodes]
for t in screen.get("object_page_tables", []) + screen.get("extra_entities", []):
    if t["entity"].upper() not in nodes:
        missing.append(t["entity"])

# 5. where do aggregated values go? -----------------------------------
# For every aggregating view: which views read its aggregated fields (through
# an association path such as _LastReqHead.MaxApprovalNum), under which name
# they expose them, and which later WHERE conditions use that name.
readers = {}
for e in edges:
    readers.setdefault(e["dst"].upper(), set()).add(e["src"].upper())


def downstream(key):
    seen, stack = set(), [key]
    while stack:
        for user in readers.get(stack.pop(), ()):
            if user not in seen:
                seen.add(user)
                stack.append(user)
    return seen


for key, node in nodes.items():
    agg = node.get("aggregation")
    if not agg:
        continue
    out_fields = {f["alias"] or f["expr"] for f in agg["functions"]}
    exposed = set(out_fields)
    refs = {}
    reach = downstream(key)
    for user in sorted(reach):
        user_ent = entity(user)
        if user_ent is None:
            continue
        for a in user_ent["associations"]:
            if a["target"].upper() != key:
                continue
            pattern = r"\b%s\.(\w+)(?:\s+as\s+(\w+))?" % re.escape(a["alias"])
            for m in re.finditer(pattern, TEXTS[user], re.I):
                if m.group(1) in out_fields:
                    name = m.group(2) or m.group(1)
                    exposed.add(name)
                    views = refs.setdefault((f"{a['alias']}.{m.group(1)}", name), [])
                    if user_ent["name"] not in views:
                        views.append(user_ent["name"])
    conditions = []
    for user in sorted(reach):
        user_ent = entity(user)
        if user_ent is None or not user_ent.get("where"):
            continue
        for term in re.split(r"\band\b|\bor\b", user_ent["where"], flags=re.I):
            term = _collapse(term.strip(" ()"))
            if any(re.search(r"\b%s\b" % re.escape(n), term) for n in exposed):
                item = {"view": user_ent["name"], "condition": term}
                if item not in conditions:
                    conditions.append(item)
    agg["usage"] = {
        "reads": [{"ref": ref, "exposed_as": name, "views": views}
                  for (ref, name), views in refs.items()],
        "conditions": conditions,
        "outside_readers": [],
    }

# Views outside the model that mention an aggregating view or one of its
# direct readers. Printed as a lead for writing the note; not drawn.
aggregating = [k for k, n in nodes.items() if n.get("aggregation")]
if aggregating:
    watch = {}
    for key in aggregating:
        for name in {key} | readers.get(key, set()):
            watch.setdefault(name, key)
    patterns = {name: re.compile(r"\b%s\b" % re.escape(name), re.I) for name in watch}
    found = {}
    for other, path in index.items():
        if other in nodes:
            continue
        try:
            source = open(path, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            continue
        for name, pattern in patterns.items():
            if pattern.search(source):
                found.setdefault((watch[name], name), []).append(other)
    for (key, name), views in found.items():
        nodes[key]["aggregation"]["usage"]["outside_readers"].append(
            {"of": nodes[name]["name"], "views": sorted(views)})

model = {
    "program_package": program_package,
    "screen": screen,
    "nodes": list(nodes.values()),
    "edges": edges,
    "external_associations": external_assocs,
    "abstract": {n: entity(n) for n in abstract},
}
os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(model, f, ensure_ascii=False, indent=1)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
by_cat = {}
for n in nodes.values():
    by_cat.setdefault(n["category"], []).append(n["name"])
print(f"repo: {len(index)} CDS sources, {len(tables)} tables")
for cat, names in by_cat.items():
    print(f"{cat} ({len(names)}): {', '.join(names)}")
kinds = {}
for e in edges:
    kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
print("edges:", kinds, "| external associations:", len(external_assocs),
      "| abstract:", len(abstract))
for name in missing:
    where = index.get(name.upper())
    print(f"WARNING: 화면 엔터티 {name}가 모델에 없다."
          + (f" 저장소의 다른 패키지에 있다: {os.path.dirname(where)}. "
             "그 패키지가 이 프로그램의 실제 패키지인지 확인하고 다시 실행한다."
             if where else " 저장소에 CDS 소스가 없다. 이름(별칭)을 확인한다."))
# Aggregating views: the facts needed to write "aggregation_notes" in sections.json.
for n in nodes.values():
    agg = n.get("aggregation")
    if not agg:
        continue
    print(f"AGGREGATION {n['name']} ({n.get('label', '')})")
    for f in agg["functions"]:
        print(f"    {f['expr']} -> {f['alias'] or '(no alias)'}")
    print(f"    group by: {', '.join(agg['group_by']) or '(none)'}")
    if agg["where"]:
        print(f"    where: {agg['where']}")
    for read in agg.get("usage", {}).get("reads", []):
        print(f"    read by {', '.join(read['views'])}: {read['ref']} as {read['exposed_as']}")
    for cond in agg.get("usage", {}).get("conditions", []):
        print(f"    condition in {cond['view']}: {cond['condition']}")
    for j in agg.get("join_conditions", []):
        print(f"    join {j['target']} on: {j['on']}")
    for item in agg.get("usage", {}).get("outside_readers", []):
        print(f"    outside the model, {item['of']} is mentioned by: {', '.join(item['views'])}")
    if not agg.get("usage", {}).get("reads"):
        print("    (no association read found: open the views that select from it to see "
              "how the value is used)")
