# Structure Redesigner

Flatten → Design → Rebuild → Enrich 방식의 레이아웃 구조 재설계 시스템

## 개요

기존 raw_data의 구조를 유지하는 대신, **이미지 기반 멀티모달 분석**으로 전체 구조를 새로 설계합니다.

```
raw_data.json + 이미지 → 구조 재설계 → redesigned_output.json
```

## 파이프라인

```
📋 Step 1: Flatten     모든 요소를 절대좌표로 평탄화 (룰베이스)
🎨 Step 2: Design      LLM이 이미지를 보고 새 구조 설계 (멀티모달)
🏗️ Step 3: Rebuild     설계된 구조로 JSON 재구성 (룰베이스)
✨ Step 4: Enrich      각 Agent가 이미지를 보고 속성 설정 (멀티모달)
```

## 설치

```bash
pip install -r requirements.txt
export OPENAI_API_KEY='your-api-key'
```

## 실행

```bash
# 기본 실행 (이미지 포함)
python structure_redesigner.py --image data/objects.png

# 병렬 처리 (Enrich 단계)
python structure_redesigner.py --image data/objects.png --parallel

# 동시 요청 수 조절 (기본: 10)
python structure_redesigner.py --image data/objects.png --parallel --concurrent 5

# 구조만 재설계 (Enrich 스킵)
python structure_redesigner.py --image data/objects.png --skip-enrich
```

## CLI 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--image <경로>` | 참조 이미지 경로 (멀티모달 분석용) | 없음 |
| `--input <경로>` | 입력 JSON 경로 | `data/raw_data.json` |
| `--output <경로>` | 출력 JSON 경로 | `data/redesigned_output.json` |
| `--parallel` | 병렬 처리 활성화 (Step 4) | 비활성화 |
| `--concurrent <N>` | 최대 동시 요청 수 | 10 |
| `--skip-enrich` | Enrich 단계 스킵 | 실행 |

---

## 파이프라인 상세

### Step 1: Flatten (룰베이스)

모든 leaf 노드를 **절대좌표**로 변환하여 flat list로 추출

```json
// 입력: 중첩된 구조
{
  "children": [
    {"children": [{"id": "bg", "position": {"x": 0, "y": 0}}]}
  ]
}

// 출력: flat list (절대좌표)
[
  {"id": "bg", "abs_position": {"x": 100, "y": 50, "width": 400, "height": 300}}
]
```

### Step 2: Design (멀티모달 LLM)

**이미지 + flat 요소 목록**을 LLM에게 전달하여 새 구조 설계

- `prompts/role_validation.yaml`의 Role 정의 참조
- 시각적 의미에 따라 그룹화
- Separator(+, - 등)는 별도 분리

```json
{
  "root": {
    "type": "HStack",
    "role": "Role.LayoutContainer.Description",
    "children": [
      {"element_id": "bg_id", "role": "Role.Element.Background"},
      {"id": "marker_group", "type": "Group", "children": [...]}
    ]
  }
}
```

### Step 3: Rebuild (룰베이스)

설계된 구조대로 JSON 재구성 (**상대좌표** 변환)

- 그룹의 bounding box 계산
- 자식 좌표를 부모 기준 상대좌표로 변환

### Step 4: Enrich (멀티모달 LLM)

각 Agent가 **이미지를 보고** 속성 설정 (YAML 프롬프트 사용)

| Agent | YAML 파일 | 설정 속성 |
|-------|-----------|----------|
| Resizing Agent | `resizing.yaml` | `resizing` (fill * fill, hug * hug 등) |
| Layout Agent | `layout.yaml` | `direction`, `gap`, `padding` |
| Alignment Agent | `alignment.yaml` | `alignment`, `verticalAlignment`, `horizontalAlignment` |

---

## 병렬 처리

`--parallel` 옵션 사용 시 **depth별 병렬 처리**:

```
Depth 0: [Root]           → 처리
    ↓
Depth 1: [A, B, C]        → 병렬 처리 (동시에)
    ↓
Depth 2: [A1, A2, B1, C1] → 병렬 처리 (동시에)
    ↓
Depth 3: [...]            → 병렬 처리 (동시에)
```

- 같은 depth의 노드들은 **동시에** 처리
- 다른 depth는 **순차적**으로 처리 (부모 → 자식 순서 보장)
- `--concurrent N`으로 동시 API 호출 수 제한

---

## 프로젝트 구조

```
tg/
├── structure_redesigner.py   # 메인 파이프라인 (Flatten→Design→Rebuild→Enrich)
├── llm_only_system.py        # 레거시 시스템 (raw_data 패칭 방식)
├── prompt_loader.py          # YAML 프롬프트 로더
├── json_utils.py             # JSON 유틸리티
│
├── prompts/                  # 프롬프트 정의 (YAML)
│   ├── role_validation.yaml  # Role 정의 + 구조 설계 규칙 (Step 2)
│   ├── resizing.yaml         # Resizing Agent 프롬프트 (Step 4)
│   ├── layout.yaml           # Layout Agent 프롬프트 (Step 4)
│   └── alignment.yaml        # Alignment Agent 프롬프트 (Step 4)
│
├── docs/                     # 문서
│   └── ROLE_VALIDATION_AGENT.md  # Role Validator 상세 문서
│
├── data/                     # 데이터
│   ├── raw_data.json         # 입력 데이터
│   ├── objects.png           # 참조 이미지
│   └── redesigned_output.json # 출력 데이터
│
└── requirements.txt          # 의존성
```

---

## 프롬프트 관리

각 Agent의 프롬프트는 **YAML 파일**로 관리됩니다.

### YAML 구조

```yaml
# prompts/resizing.yaml 예시
system_role: |
  당신은 레이아웃 시스템 전문가입니다.
  
  ⭐ 멀티모달 분석:
  이미지가 제공되면, 이미지를 보고 해당 요소의 실제 크기 조절 방식을 판단하세요.

task_description: |
  다음 노드에 적절한 resizing 규칙을 결정하세요.

prompt_template: |
  ## Resizing 결정
  {node_info}
  {output_format}

output_format: |
  ```json
  {"resizing": "fill * hug", "reason": "이유"}
  ```

llm_config:
  model: "gpt-4o"
  temperature: 0.2
  max_tokens: 200
```

### 프롬프트 수정

코드 수정 없이 YAML 파일만 편집:

```bash
vi prompts/resizing.yaml   # Resizing 프롬프트 수정
vi prompts/layout.yaml     # Layout 프롬프트 수정
vi prompts/alignment.yaml  # Alignment 프롬프트 수정
```

---

## Role 정의

`prompts/role_validation.yaml`에서 정의된 Role 사용:

### LayoutContainer Roles
| Role | 설명 |
|------|------|
| `Role.LayoutContainer.Description` | 설명 컨테이너 |
| `Role.LayoutContainer.Marker` | 마커 컨테이너 (아이콘 + 배경) |
| `Role.LayoutContainer.Decoration` | 장식 컨테이너 |
| `Role.LayoutContainer.Title` | 제목 컨테이너 |
| `Role.LayoutContainer.Subtitle` | 부제목 컨테이너 |

### Element Roles
| Role | 설명 | 제약 |
|------|------|------|
| `Role.Element.Background` | 배경 요소 | 부모당 1개만 |
| `Role.Element.Decoration` | 장식 요소 | 겹침 불가 |
| `Role.Element.Separator` | 분리 요소 (+, - 등) | - |
| `Role.Element.Marker` | 마커 요소 | - |
| `Role.Element.Title` | 제목 텍스트 | - |
| `Role.Element.Description` | 설명 텍스트 | - |

### Layout Types
| Type | 설명 |
|------|------|
| `HStack` | 가로 배열 |
| `VStack` | 세로 배열 |
| `Group` | 비정형 그룹 (겹치는 요소들) |
| `Grid` | 격자 배열 |

---

## 참고 문서

- [Role Validation Agent 상세](docs/ROLE_VALIDATION_AGENT.md)
