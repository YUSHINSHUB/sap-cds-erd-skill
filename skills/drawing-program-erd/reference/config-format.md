# 설정 파일 형식

두 파일 모두 UTF-8 JSON이다. `examples/`에 데모 프로젝트(주문 승인)에서 쓴 파일이 있다.

## screen.json — 화면에 무엇이 뜨는가

`frontend_screen.py`가 초안을 만든다. 틀린 곳은 직접 고친다.

| 키 | 내용 |
|---|---|
| `program` | 제목에 쓰는 프로그램 ID |
| `app`, `app_title` | 프론트엔드 앱 ID와 제목 |
| `tabs` | 화면의 목록(탭). `{"key": "tab1", "entity": "…", "title": "…"}`. 순서가 그림의 위→아래 순서다. **최종 출력 열에 놓이는 것은 이것뿐이다** |
| `outputs` | 탭이 없을 때 최종 출력으로 삼을 엔터티. `{"entity": "…", "title": "…"}`. 비우면 자동으로 정한다 |
| `object_page_tables` | 상세 화면의 하위 테이블. `{"entity": "…", "via": "_Alias", "of": ["tab1"]}`. 가운데 구역에 놓이고 상자에 "상세 화면 테이블"로 표시된다 |
| `extra_entities` | 탭은 아니지만 앱 코드가 직접 읽는 엔터티. `{"entity": "…", "used_by": "…"}`. 다른 패키지에 있어도 그림에 들어가고, 상자에 "앱에서 직접 조회"와 `used_by`가 표시된다 |
| `service_exposed` | 서비스 정의가 노출하는 엔터티 이름 목록 |
| `evidence` | 각 항목을 어느 파일에서 확인했는지 |
| `review` | 스크립트가 남긴 확인 사항. 처리한 뒤 지워도 된다 |

- 프리스타일 UI5 앱은 `tabs`가 비어서 나온다. view XML의 테이블·리스트 바인딩 경로를 읽고 직접 채운다.
- `extra_entities`는 주석을 뺀 코드에서 찾는다. 그래도 정의만 있고 호출되지 않는 함수일 수 있으니 코드를 열어 확인하고, 쓰이지 않으면 지우거나 `used_by`에 그 사실을 적는다.

## sections.json — 기능 구분과 집계 값의 의미

사람이(에이전트가) 문서를 읽고 쓴다. 스크립트가 만들어 주지 않는다. 최상위 키는 세 개다.

```json
{
  "evidence": [
    "어느 문서의 어느 부분을 근거로 나눴는지 한 줄씩 (파일 전체에 대한 근거 목록이다)"
  ],
  "aggregation_notes": {
    "ZDEMO_I_ORDER_APPR_LAST": "주문별 가장 큰 승인번호 = 그 주문의 가장 최근 승인 1건. 승인 이력이 여러 건일 때 최신 건만 고르는 기준값"
  },
  "sections": [
    {
      "id": "header",
      "title": "① 주문 헤더",
      "note": "주문 테이블에 고객 이름을 붙인 화면용 BO. 탭1의 기준",
      "entities": ["ZDEMO_I_ORDER", "ZDEMO_R_ORDER"]
    }
  ]
}
```

### sections

- 배열 순서가 위→아래 순서다. 서로 많이 연결된 구역을 이웃하게 둔다.
- `entities`에는 **가운데 구역에 놓이는 view를 모두** 적는다.
  - 포함: 패키지의 중간 view, 패키지 밖의 중간 view, 상세 화면 테이블, `extra_entities`
  - 제외: Source(표준 view · DB 테이블)와 `tabs`의 엔터티. 적지 않아도 관련 구역 높이에 자동으로 맞춰진다
- 어느 구역에도 없는 view는 `기타` 구역으로 모인다. `build_erd.py` 출력의 section 목록에서 확인한다.
- 구역 기준은 문서에 나온 업무 기능이다. 탭 기준으로 나누면 여러 탭이 함께 쓰는 view가 갈 곳이 없어지므로, 탭과 기능이 일치할 때만 탭 기준으로 한다.
- 기능 구분을 하지 않으려면 `"sections": []`로 둔다. 이때도 `aggregation_notes`는 쓴다.

### aggregation_notes

키는 집계 view 이름, 값은 "가져오는 값" 설명이다. 그림에는 이렇게 나온다.

```
Σ 집계  GROUP BY OrderId                                           ← 자동
  MAX(ApprovalNo) → LastApprovalNo (최댓값)                        ← 자동
  집계 대상 조건: …                                                 ← 자동 (WHERE)
  집계 대상 조건 (join X): …                                        ← 자동 (join의 ON)
▶ 가져오는 값  주문별 가장 큰 승인번호 = …                          ← aggregation_notes
▶ 읽는 곳  ZDEMO_R_ORDER : _LastAppr.LastApprovalNo                ← 자동
▶ 조건에 사용  ZDEMO_C_ORDER_APPR_HIS : _Appr.ApprovalNo = _Last.LastApprovalNo   ← 자동
```

좋은 설명은 세 가지를 담는다: **무엇을 묶어서**(GROUP BY의 업무적 의미), **어떤 값이 나오고**, **그 값이 어디서 무슨 뜻으로 쓰이는지**.

`cds_model.py`의 `AGGREGATION` 블록이 주는 실마리:

| 줄 | 뜻 |
|---|---|
| `read by … : _Alias.Field as Name` | association으로 값을 읽는 view와 노출 이름 |
| `condition in … : …` | 그 값을 쓰는 WHERE 조건 |
| `join X on: …` | 집계 대상을 걸러 내는 join 조건 |
| `outside the model, V is mentioned by: …` | 패키지 밖에서 이 값을 쓰는 view. 그림에는 안 나오므로, 화면이 아니라 다른 프로그램이 쓰는 값이면 설명에 적는다 |
| `(no association read found …)` | 값을 `select from`으로 읽는다는 뜻이다. 그 view의 소스를 열어 확인한다 |

## 그림에 들어가는 것과 빠지는 것

- 본 그림(1페이지)의 상자는 **데이터 원천을 따라간 것**(`select from` / `projection on` / `join` / `union`)과 패키지 view다.
- association으로만 닿는 패키지 밖 대상(value help, 텍스트, 표준 view)은 상자로 그리지 않고 마지막 페이지의 "외부 연관" 표에 넣는다. 그중에 실제 데이터 원천 역할을 하는 것이 있으면 보고에 적는다.

## 자동 추출의 한계

- 정규식 기반이다. `select from` / `projection on` / `join` / `union` / `association` / `composition` / `redirected to` / 집계 함수 / `group by` / `where` / join `on`을 읽는다.
- union view는 첫 번째 select의 키·집계·조건만 본다. 데이터 원천은 모든 branch를 따라간다.
- 테이블 함수와 커스텀 엔터티는 데이터 소스가 없는 것으로 보고 Source 열에 놓는다.
- 상자 색은 view 이름의 `Z<모듈>_<R|I|C|P>_…` 규칙으로 정한다. 다른 이름 규칙이면 모두 Interface 색으로 나온다.
- 로컬 abapGit 소스 기준이다. 시스템의 현재 상태와 다를 수 있다.
