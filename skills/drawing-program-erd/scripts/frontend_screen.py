"""Draft screen.json: which entities does the program put on screen?

Usage:
    python frontend_screen.py <backend package dir> <out screen.json> [frontend app dir]

Reads (nothing is modified):
  backend  *.srvd.srvdsrv      -> entities the service exposes
           *.ddlx.asddlxs      -> object-page tables (#LINEITEM_REFERENCE facets)
  frontend webapp/manifest.json, annotations/*.xml, i18n/i18n.properties
                                -> list-report tabs and their titles, object pages
           webapp/**/*.ts|js|xml -> other exposed entities the code reads

The result is a DRAFT. Read the "review" list in the output and correct the
file by hand before building the model (freestyle apps always need this).
"""
import glob
import json
import os
import re
import sys

package_dir, out_path = sys.argv[1:3]
frontend = sys.argv[3] if len(sys.argv) > 3 else None
review = []


def read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(//|--).*", "", text)


package = os.path.basename(os.path.normpath(package_dir))
program = package.upper()[1:] if package.upper().startswith(("Z", "Y")) else package.upper()

# ── backend: service exposure ────────────────────────────────────────

exposed, alias_to_entity, services = [], {}, []
for path in sorted(glob.glob(os.path.join(package_dir, "*.srvd.srvdsrv"))):
    text = strip_comments(read(path))
    name = re.search(r"define\s+service\s+(\w+)", text, re.I)
    services.append(name.group(1) if name else os.path.basename(path))
    for m in re.finditer(r"\bexpose\s+(\w+)(?:\s+as\s+(\w+))?", text, re.I):
        if m.group(1) not in exposed:
            exposed.append(m.group(1))
        alias_to_entity[(m.group(2) or m.group(1)).upper()] = m.group(1)
if not services:
    review.append("패키지에 서비스 정의(*.srvd)가 없다. 다른 패키지의 서비스가 이 view들을 "
                  "노출하는지 확인하고 service_exposed를 직접 채운다.")


def entity_of(entity_set):
    """Map an OData entity set name (possibly an alias) to the CDS entity."""
    return alias_to_entity.get((entity_set or "").upper(), entity_set)


# ── backend: object page tables from metadata extensions ─────────────

def source_of(entity):
    path = os.path.join(package_dir, entity.lower() + ".ddls.asddls")
    return strip_comments(read(path)) if os.path.isfile(path) else None


def association_target(entity, alias, depth=0):
    """Follow an association alias to its target, through projection bases."""
    text = source_of(entity)
    if text is None or depth > 6:
        return None
    patterns = [
        r"\bassociation\b[^;{]*?\bto\s+(?:parent\s+)?(\w+)\s+as\s+%s\b",
        r"\bcomposition\b[^;{]*?\bof\s+(\w+)\s+as\s+%s\b",
        r"\b%s\s*:\s*redirected\s+to\s+(?:composition\s+child\s+|parent\s+)?(\w+)",
        r"\b%s\s*:\s*composition\s*(?:\[[^\]]*\])?\s*of\s+(\w+)",
    ]
    for pattern in patterns:
        m = re.search(pattern % re.escape(alias), text, re.I)
        if m:
            return m.group(1)
    base = re.search(r"as\s+(?:projection\s+on|select\s+(?:distinct\s+)?from)\s+(\w+)",
                     text, re.I)
    return association_target(base.group(1), alias, depth + 1) if base else None


facets = []   # (annotated entity, association alias, target entity)
for path in sorted(glob.glob(os.path.join(package_dir, "*.ddlx.asddlxs"))):
    text = strip_comments(read(path))
    annotated = re.search(r"annotate\s+(?:entity|view)\s+(\w+)\s+with", text, re.I)
    if not annotated:
        continue
    for block in re.finditer(r"\{[^{}]*#LINEITEM_REFERENCE[^{}]*\}", text, re.S):
        target_element = re.search(r"targetElement\s*:\s*'(\w+)'", block.group(0))
        if target_element:
            alias = target_element.group(1)
            facets.append((annotated.group(1), alias,
                           association_target(annotated.group(1), alias)))

# ── frontend ─────────────────────────────────────────────────────────

screen = {
    "program": program,
    "app": None, "app_title": "", "frontend_type": None,
    "evidence": {"service": ", ".join(services),
                 "object_page_tables": "backend *.ddlx.asddlxs (#LINEITEM_REFERENCE facets)"},
    "tabs": [], "object_page_tables": [], "extra_entities": [],
    "service_exposed": exposed,
}
tab_entities = {}        # entity -> tab key
object_page_entities = []

if frontend and os.path.isfile(os.path.join(frontend, "webapp", "manifest.json")):
    webapp = os.path.join(frontend, "webapp")
    manifest = json.loads(read(os.path.join(webapp, "manifest.json")))
    i18n = {}
    props = os.path.join(webapp, "i18n", "i18n.properties")
    if os.path.isfile(props):
        for line in read(props).splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                i18n[key.strip()] = value.strip()

    def text_of(value):
        m = re.fullmatch(r"\{\{(.+?)\}\}|\{@?i18n>(.+?)\}", (value or "").strip())
        if m:
            return i18n.get(m.group(1) or m.group(2), value)
        return value or ""

    app = manifest.get("sap.app", {})
    screen["app"] = app.get("id")
    screen["app_title"] = text_of(app.get("title"))

    # Which service does the app really call? App name and package number can
    # differ, so the package is confirmed from the service URI, not the name.
    called = []
    for source in app.get("dataSources", {}).values():
        m = re.search(r"/srvd(?:_a2x)?/sap/(\w+)/", source.get("uri", ""))
        if m and m.group(1).upper() not in called:
            called.append(m.group(1).upper())
    screen["evidence"]["frontend_service"] = ", ".join(called)
    known = {s.upper() for s in services}
    repo = os.path.abspath(package_dir)
    while os.path.basename(repo).lower() != "src" and os.path.dirname(repo) != repo:
        repo = os.path.dirname(repo)
    for service in called:
        if service in known:
            continue
        hits = glob.glob(os.path.join(repo, "**", service.lower() + ".srvd.srvdsrv"),
                         recursive=True)
        review.append(
            f"프론트엔드가 호출하는 서비스 {service}는 이 패키지에 없다. "
            + (f"저장소의 다른 패키지에 있다: {os.path.dirname(hits[0])} — "
               "그 패키지로 이 스크립트를 다시 실행한다." if hits else
               "저장소에서도 찾지 못했다. 서비스 이름과 패키지를 확인한다."))

    variant_titles = {}   # SelectionVariant qualifier -> title
    for xml_path in glob.glob(os.path.join(webapp, "annotations", "*.xml")):
        xml = read(xml_path)
        for m in re.finditer(
                r'Term="UI\.Selection(?:Presentation)?Variant"\s+Qualifier="(\w+)"(.*?)</Annotation>',
                xml, re.S):
            title = re.search(r'Property="Text"\s+String="([^"]*)"', m.group(2))
            if title:
                variant_titles[m.group(1)] = text_of(title.group(1))

    targets = manifest.get("sap.ui5", {}).get("routing", {}).get("targets", {})
    fiori_elements = False
    for target in targets.values():
        name = target.get("name", "")
        settings = target.get("options", {}).get("settings", {})
        main = entity_of(settings.get("entitySet")
                         or (settings.get("contextPath") or "").strip("/"))
        if "ListReport" in name or "AnalyticalListPage" in name:
            fiori_elements = True
            paths = settings.get("views", {}).get("paths", [])
            if not paths:
                paths = [{"key": "tab1"}]
            for i, p in enumerate(paths):
                entity = entity_of(p.get("entitySet")
                                   or (p.get("contextPath") or "").strip("/")) or main
                qualifier = (p.get("annotationPath") or "").split("#")[-1]
                title = variant_titles.get(qualifier) or (
                    screen["app_title"] if len(paths) == 1 else qualifier)
                key = f"tab{i + 1}"
                screen["tabs"].append({"key": key, "entity": entity, "title": title})
                tab_entities.setdefault(entity, key)
        elif "ObjectPage" in name and main:
            fiori_elements = True
            object_page_entities.append(main)
    screen["frontend_type"] = (
        "Fiori Elements" + (f" ({len(screen['tabs'])} tab)" if screen["tabs"] else "")
        if fiori_elements else "freestyle UI5")
    screen["evidence"]["tabs"] = ("webapp/manifest.json > sap.ui5.routing.targets "
                                  "(entitySet, views.paths) + annotations/*.xml + i18n")

    # other exposed entities the app code reads directly
    code_files = []
    for ext in ("ts", "js", "xml"):
        code_files += glob.glob(os.path.join(webapp, "**", f"*.{ext}"), recursive=True)
    code_files = [f for f in code_files
                  if not re.search(r"[\\/](localService|test|node_modules|annotations)[\\/]", f)]
    used = {}
    for path in code_files:
        text = read(path)
        if path.endswith(".xml"):           # ignore commented-out code
            text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        else:
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            text = re.sub(r"(?m)^\s*//.*$", "", text)
        for set_name, entity in alias_to_entity.items():
            if re.search(r"/%s\b" % re.escape(set_name), text, re.I):
                used.setdefault(entity, []).append(os.path.basename(path))
    for entity, files in used.items():
        if entity in tab_entities or entity in object_page_entities:
            continue
        screen["extra_entities"].append(
            {"entity": entity, "used_by": ", ".join(sorted(set(files)))})
    screen["evidence"]["extra_entities"] = "webapp code: paths to exposed entity sets"

    if not fiori_elements:
        review.append("프리스타일 앱이다. manifest에서 탭을 읽을 수 없으므로 view XML과 "
                      "컨트롤러를 읽고 화면에 목록으로 뜨는 엔터티를 tabs에 직접 적는다. "
                      "extra_entities에 후보가 있다.")
    if any(not t["title"] or t["title"].startswith("SV_") for t in screen["tabs"]):
        review.append("탭 제목을 찾지 못한 탭이 있다. 백엔드 CDS의 @UI.selectionVariant나 "
                      "i18n에서 제목을 찾아 title을 고친다.")
else:
    if frontend:
        review.append(f"프론트엔드 폴더에서 webapp/manifest.json을 찾지 못했다: {frontend}")
    apps = [os.path.basename(p).split(".")[0]
            for p in glob.glob(os.path.join(package_dir, "*.uiad.json"))]
    if apps:
        review.append(f"패키지에 UI5 앱 정의({', '.join(apps)})가 있다. 프론트엔드가 있는 "
                      "프로그램이다. 앱 소스 폴더를 찾아 세 번째 인자로 주고 다시 실행한다. "
                      "폴더 이름의 번호가 패키지와 다를 수 있다.")
    review.append("프론트엔드 없이 진행한다. tabs가 비어 있으면 빌드 스크립트가 최종 출력을 "
                  "'서비스가 노출하고 다른 view가 읽지 않는 엔터티'로 정한다. 다르게 잡으려면 "
                  "outputs에 {\"entity\", \"title\"}를 직접 적는다.")

for entity, alias, target in facets:
    if target is None:
        review.append(f"{entity}의 상세 테이블 {alias}가 가리키는 엔터티를 찾지 못했다.")
        continue
    shown_in = tab_entities.get(entity)
    if shown_in is None and screen["tabs"]:
        continue          # the annotated entity is not on screen
    entry = next((t for t in screen["object_page_tables"]
                  if t["entity"] == target and t["via"] == alias), None)
    if entry is None:
        entry = {"entity": target, "via": alias, "of": []}
        screen["object_page_tables"].append(entry)
    label = shown_in or entity
    if label not in entry["of"]:
        entry["of"].append(label)
        entry["of"].sort()

screen["review"] = review
os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(screen, f, ensure_ascii=False, indent=2)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print(f"program {program} | service: {', '.join(services) or '(none)'} "
      f"| exposed: {len(exposed)} | frontend: {screen['frontend_type']}")
for t in screen["tabs"]:
    print(f"  {t['key']}: {t['entity']} — {t['title']}")
for t in screen["object_page_tables"]:
    print(f"  object page table: {t['entity']} via {t['via']} of {', '.join(t['of'])}")
for t in screen["extra_entities"]:
    print(f"  also read by the app: {t['entity']} ({t['used_by']})")
for item in review:
    print("  REVIEW:", item)
