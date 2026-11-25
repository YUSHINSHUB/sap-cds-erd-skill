---
name: drawing-program-erd
description: SAP RAP 프로그램(패키지) 단위로 CDS view ERD를 그려 달라는 요청에 사용한다. "SD0100 ERD 만들어줘", "이 프로그램 CDS view 관계도 그려줘", "view 구조를 그림으로", "테이블에서 화면까지 어떻게 이어지는지 보여줘"처럼 프로그램 ID나 패키지를 주고 CDS view 사이의 관계와 데이터 흐름 그림을 원할 때 쓴다. 테이블 필드 단위 DB 설계도나 CDS 한두 개의 문법 설명에는 쓰지 않는다.
---

# 프로그램 단위 CDS ERD 그리기

## 개요

한 장에서 **왼쪽 Source(표준 CDS view · DB 테이블) → 가운데 CDS View → 오른쪽 최종 출력(화면 탭)** 으로 흐르게 그린다.

역할을 나눈다. 스크립트는 소스에서 **사실**(관계, 집계, 사용처)을 뽑고 위치를 계산한다. 사람이 읽어야 아는 **해석**(기능 구분, 집계 값의 의미)은 문서를 읽고 설정 파일에 적는다. 그림은 전부 `cli-anything-drawio`가 그린다.

결과물은 최대 3페이지다: ① View 관계(본 그림) ② Abstract 엔터티(액션 파라미터 구조, 있을 때만) ③ 외부 연관 표(association으로만 닿는 패키지 밖 대상, 있을 때만).

## 준비

| 항목 | 찾는 법 |
|---|---|
| 백엔드 `src` 폴더 | 로컬에 받아 둔 abapGit 저장소의 `src`. 모르면 사용자에게 묻는다 |
| 패키지 폴더 | `src` 아래에서 프로그램 ID 폴더를 찾는다 (예: `src/zdemo010/zdemo0100`) |
| 프론트엔드 앱 폴더 | 없을 수 있다. 패키지 안의 `*.uiad.json` 이름이 앱 이름이다. 폴더 번호가 패키지와 다를 수 있으므로, 프론트엔드 폴더들의 `webapp/manifest.json`에서 패키지의 서비스 정의 이름(`*.srvd.srvdsrv`)을 검색해 찾는다 |
| draw.io CLI | `cli-anything-drawio backend info`에서 `found: yes`. 명령을 못 찾으면 `python -c "import sysconfig; print(sysconfig.get_path('scripts'))"`와 `…get_path('scripts','nt_user')`가 알려 주는 폴더에서 실행 파일을 찾아 전체 경로로 쓴다. 설치되어 있지 않으면 이 스킬 저장소 README의 설치 절차를 사용자에게 안내한다 |
| OpenWiki (선택) | 문서 조사에 가장 먼저 쓴다. 백엔드 `src`가 있는 저장소 루트에 `openwiki/` 폴더가 있으면 쓴다(사용자가 위키 경로를 주면 그 경로). openwiki MCP가 연결되어 있으면 `openwiki_search` · `openwiki_read`로 읽되 결과가 이 저장소의 위키인지 확인하고, 없으면 `openwiki/` 아래 페이지 md를 직접 읽는다. `.claims` 같은 OpenWiki 내부 파일은 근거로 쓰지 않는다. 위키는 사용자가 스킬을 쓰기 전에 `src`에 맞춰 최신으로 갱신해 둔다는 전제다. 그러므로 위키와 `src`는 서로 맞다고 보고 그대로 믿는다. 위키가 없으면 건너뛴다 |
| basic-memory | 문서 조사에 쓴다. `bm_project(command="list")`로 프로젝트 이름을 확인한다. 연결되어 있지 않으면 사용자에게 알리고 로컬 문서만으로 진행한다 |
| 출력 폴더 | 사용자가 정하지 않았으면 한 번 묻는다 |

- 명령은 출력 폴더에서 실행한다. 중간 파일은 그 아래 `work/`에 둔다(스크립트가 폴더를 만든다). 스크립트는 이 `SKILL.md`와 같은 폴더의 `scripts/`에 있다.
- 이전 작업에서 만든 같은 이름의 ERD가 있으면 덮어쓰지 말고 새 이름(`-v2`)으로 만든다. 이번 작업 안에서 다시 생성하는 것은 덮어써도 된다.

## 절차

**1. 화면 엔터티 확인**

```bash
python <스킬>/scripts/frontend_screen.py <패키지 폴더> work/screen.json [<프론트엔드 앱 폴더>]
```

`REVIEW` 줄을 모두 처리한다. `also read by the app` 줄은 해당 코드를 열어 실제로 호출되는지 확인한다. `screen.json`은 초안이므로 직접 고쳐도 된다.

**2. 모델 추출**

```bash
python <스킬>/scripts/cds_model.py <src 폴더> <패키지 폴더> work/screen.json work/model.json
```

`WARNING`이 있으면 멈추고 원인을 고친다. `AGGREGATION` 블록은 4번에서 쓴다.

**3. 문서 조사**

- OpenWiki(준비 표의 조건): `index.md`에서 시작해 모듈 목차(예: `modules/sd/index.md`)를 거쳐, 프로그램 ID나 핵심 엔터티 이름이 나오는 페이지를 찾아 읽는다(폴더 안 md를 이름 · 내용으로 검색해도 된다). 그 페이지가 링크하는 페이지 중 이 프로그램의 구조를 설명하는 것(architecture · common 페이지 등)까지 따라가 읽는다. MCP가 있으면 아래 검색어로 `openwiki_search`를 먼저 해도 된다. 위키의 처리 단계 · BO 노드 · 패턴 설명은 그대로 기능 구분과 집계 설명의 근거로 쓴다. 위키에 없는 세부(보조 view를 누가 읽는지, 집계 조건, 화면 라벨)는 `src`의 CDS · metadata extension에서 읽는다.
- basic-memory: OpenWiki가 있으면 그다음에, 위키에 없는 내용을 보충하려고 찾는다. 볼트에 검색 인덱스 노트(키워드 → 노트 매핑)를 두고 있으면 먼저 읽는다. 그다음 `bm_search(query=…, projects=["<프로젝트>"], mode="text")`로 프로그램 ID(`Z` 접두 포함), 앱 이름, 핵심 엔터티·테이블 이름, 한글 업무 용어를 각각 검색하고, 찾은 노트를 `bm_read`로 읽는다.
- 로컬 문서: 프론트엔드의 `docs/`, 프로젝트 스펙 폴더(하위 폴더 포함). 파일 이름이 프로그램 ID가 아닐 수 있으므로 앱 번호, 한글 업무명, 노트의 "참조" 항목으로도 찾는다. xlsx·docx는 읽기 전용으로 연다.

**4. `work/sections.json` 작성** — 형식과 예시는 [reference/config-format.md](reference/config-format.md)

- `sections`: 문서에서 기능 단위가 확인되면 가운데 구역을 기능별로 나눈다. 문서의 BO 노드 구분(Header / Item / Approval)이나 스펙의 기능 항목이 기능 단위다. 문서가 배정하지 않은 보조 view는 소스에서 누가 읽는지를 보고 가장 가까운 구역에 넣는다.
- `aggregation_notes`: 집계 view마다 **가져오는 값**을 한두 문장으로 쓴다. 무엇을 묶어서, 어떤 값이 나오고, 그 값이 화면이나 다른 view에서 무슨 뜻으로 쓰이는지. `AGGREGATION` 블록이 실마리다. 값의 화면상 의미는 한 단계로 끝나지 않으니, 읽는 view를 따라가 metadata extension의 라벨까지 확인한다.

**5. 그리기**

```bash
python <스킬>/scripts/build_erd.py work/model.json work/sections.json <cli 실행 파일> <프로그램>-erd.drawio work/commands.txt
```

기능 구분도 집계 view도 없으면 `work/sections.json` 자리에 `-`를 넣는다.

**6. 검증** — 건너뛰지 않는다

```bash
cli-anything-drawio --project <프로그램>-erd.drawio project check
cli-anything-drawio --project <프로그램>-erd.drawio export render <프로그램>-erd-p1.png --page 0 --overwrite
cli-anything-drawio --project <프로그램>-erd.drawio export render <프로그램>-erd.pdf --all-pages --crop --overwrite
```

PNG를 **직접 열어 본다**. 그림이 넓으면 통째로는 글자가 안 읽히므로 영역별로 잘라 확대해서 본다(Pillow가 있으면 `Image.crop`). 확인할 것:

- 세 구역이 맞고 최종 출력이 맨 오른쪽 열에 혼자 있는가
- 상자끼리 겹치지 않고, 선이 남의 상자 위를 지나가지 않는가
- `기타` 구역이 없는가
- 집계 상자마다 "▶ 가져오는 값"이 있는가
- 2·3페이지가 있으면 `--page 1`, `--page 2`도 뽑아 본다

고칠 것이 있으면 4~5번을 반복한다.

**7. 보고** — 화면 엔터티와 그 근거 파일, 기능 구분의 근거 문서(또는 구분하지 않은 이유), 순환 관계와 한계(정규식 추출, 로컬 소스 기준)를 함께 알린다.

## 상황별 처리

| 스크립트 출력 · 상황 | 처리 |
|---|---|
| `REVIEW: … 서비스 …는 이 패키지에 없다` | 앱 이름과 패키지 번호가 다른 경우다. 알려 준 패키지로 1번부터 다시 한다 |
| `REVIEW: 패키지에 UI5 앱 정의(…)가 있다` | 프론트엔드가 있는 프로그램이다. 앱 폴더를 찾아 인자로 주고 다시 한다 |
| `REVIEW: 프리스타일 앱` | view XML과 컨트롤러를 읽고 목록으로 뜨는 엔터티를 `tabs`에 직접 적는다 |
| 프론트엔드가 정말 없음 | 인자를 생략한다. 최종 출력은 "서비스가 노출하고 다른 view가 읽지 않는 view"로 잡힌다. 다르게 잡으려면 `screen.json`의 `outputs`에 적는다 |
| `WARNING: 화면 엔터티 …가 모델에 없다` | 패키지가 틀렸거나 이름이 별칭이다. 고치고 2번을 다시 한다 |
| OpenWiki에 해당 프로그램 내용이 없음 | 위키는 건너뛰고 basic-memory · 로컬 문서 조사로 넘어간다 |
| 문서에서 기능 단위를 확인하지 못함 | 구분 없이 그린다. view 이름만 보고 나누지 않는다 |
| 출력에 `기타` 구역이 생김 | `sections.json`에서 빠진 view다. 상세 화면 테이블과 `extra_entities`도 가운데 view이므로 구역에 넣어야 한다. 문서 근거가 없는 view만 `기타`에 남기고 보고한다 |
| `NOTE: 순환 관계` | 서로를 참조하는 view다. 그대로 두고 보고에 적는다 |
| `NOTE: … 같은 열이라 직선으로` | 그 선이 상자를 가로지르는지 그림에서 확인하고 보고에 적는다 |
| 열이 많아 그림이 지나치게 넓음 | 그대로 두고 PDF를 함께 준다. 임의로 view를 빼지 않는다 |

`build_erd.py` 출력의 `crossings`는 선이 엇갈리는 횟수다. 구역 순서를 바꿔 다시 그렸을 때 이 숫자가 줄면 그 순서가 낫다.

## 흔한 실수

- 앱 폴더 이름으로 백엔드 패키지를 단정한다. 패키지는 manifest의 서비스 URI로 확인한다(1번 스크립트가 해 준다).
- `<src 폴더>`에 패키지 폴더를 준다. 저장소 전체를 색인해야 패키지 밖의 view를 지나 표준 view·테이블까지 추적된다.
- 프론트엔드 폴더를 못 찾았다고 "프론트엔드 없음"으로 진행한다.
- 집계 view에 함수만 보이고 "가져오는 값"이 없다. `aggregation_notes`를 빠뜨린 것이다.
- 문서를 찾지 않고 기능 구분을 지어낸다.
- 그림을 열어 보지 않고 끝낸다.
