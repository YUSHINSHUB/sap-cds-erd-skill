# Third-party notices

이 저장소는 [MIT 라이선스](LICENSE)를 따른다. 아래는 그와 다른 조건이 붙는 부분과 외부 의존에 대한 고지다.

## CLI-Anything (HKUDS)

- 출처: https://github.com/HKUDS/CLI-Anything
- 라이선스: Apache License 2.0 — 전문은 [tools/cli-anything-drawio/LICENSE-CLI-Anything.txt](tools/cli-anything-drawio/LICENSE-CLI-Anything.txt)

| 이 저장소의 파일 | 관계 |
|---|---|
| `tools/cli-anything-drawio/cli_anything/drawio/utils/repl_skin.py` | CLI-Anything 플러그인의 `repl_skin.py`를 수정 없이 그대로 복사한 파일 |
| `tools/cli-anything-drawio/` 의 나머지 | CLI-Anything이 제시하는 방법론(HARNESS.md)과 패키지 구조(`cli_anything.<software>` 네임스페이스)를 따라 새로 작성한 코드. CLI-Anything 저장소에 들어 있는 draw.io용 CLI와는 다른 구현이다 |

## draw.io (JGraph)

이 저장소는 draw.io를 포함하지 않는다. `cli-anything-drawio`는 사용자가 따로 설치한 draw.io 데스크톱 앱을 실행해 PNG · PDF 등을 내보낸다.

- https://github.com/jgraph/drawio-desktop

## 데모 프로젝트

`examples/demo-project/`의 CDS view, 테이블, 프론트엔드 앱, 스펙 문서는 이 저장소를 위해 만든 가상의 예시다. 실제 시스템이나 고객의 개발 객체가 아니다. `I_Customer`, `I_Currency`는 SAP 표준 CDS view의 이름만 참조하며, SAP 소스는 들어 있지 않다.
