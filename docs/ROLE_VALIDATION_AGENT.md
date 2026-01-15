# Role Validation Agent 상세 가이드

## 목차
1. [개요](#1-개요)
2. [Role 시스템 정의](#2-role-시스템-정의)
3. [핵심 규칙](#3-핵심-규칙)
4. [처리 흐름](#4-처리-흐름)
5. [실제 데이터 예시](#5-실제-데이터-예시)
6. [LLM 입출력 상세](#6-llm-입출력-상세)
7. [문제 케이스와 해결](#7-문제-케이스와-해결)

---

## 1. 개요

Role Validation Agent는 JSON 레이아웃 구조에서 **각 노드의 Role이 올바르게 할당되었는지 검증**합니다.

### 무엇을 검증하나?

```
✅ 계층 구조: Page → LayoutContainer → Element 순서가 맞는지
✅ 제약 조건: Title은 1개만, Subtitle은 Title과 함께 등
✅ Background 단일성: 하나의 Group/Stack에 Background는 1개만
✅ 겹침 규칙: 겹치는 요소들은 Group으로 묶여야 함
```

### 처리 순서에서의 위치

```
[Step 0] Role Validation  ← 여기!
[Step 1] Resizing 결정
[Step 2] Layout 속성 결정
[Step 3] Alignment 결정
[Step 4] 자식 노드 재귀 처리
```

---

## 2. Role 시스템 정의

### 2.1 Role.Page (페이지 레벨) - 5개

| Role | 설명 | 예시 |
|------|------|------|
| `Role.Page.Opening` | 표지 페이지 | 제목, 발표자, 날짜 |
| `Role.Page.Agenda` | 목차 페이지 | 전체 구성 개괄 |
| `Role.Page.SectionDivider` | 섹션 구분 | 새 섹션 시작 간지 |
| `Role.Page.Ending` | 마무리 페이지 | 감사 인사, Q&A |
| `Role.Page.Content` | 본문 페이지 | 실제 내용 전달 |

### 2.2 Role.LayoutContainer (컨테이너) - 10개

| Role | 설명 | 필수 구성 |
|------|------|----------|
| `LayoutContainer.Title` | 제목 컨테이너 | Background + Title 요소 |
| `LayoutContainer.Subtitle` | 부제목 컨테이너 | Background + Subtitle 요소 |
| `LayoutContainer.Description` | 설명 컨테이너 | 2개 이상 설명 요소 |
| `LayoutContainer.Highlight` | 강조 컨테이너 | 강조된 요소 |
| `LayoutContainer.Separator` | 분리기 컨테이너 | Separator 요소 1개+ |
| `LayoutContainer.Marker` | 마커 컨테이너 | Marker 요소 필수 |
| `LayoutContainer.Decoration` | 장식 컨테이너 | 장식 요소들 |
| `LayoutContainer.Background` | 배경 컨테이너 | 배경 요소들 |
| `LayoutContainer.PageHeader` | 상단 정보 | 섹션명, 문서명 |
| `LayoutContainer.PageFooter` | 하단 정보 | 페이지 번호 |

### 2.3 Role.Element (개별 요소) - 8개

| Role | 설명 | 제약 조건 |
|------|------|----------|
| `Element.Title` | 제목 | **부모 내 1개만** |
| `Element.Subtitle` | 부제목 | Title과 함께, 최대 2개 |
| `Element.Description` | 설명 텍스트 | 단독 사용 가능 |
| `Element.Highlight` | 강조 텍스트 | 폰트 차별화 필수 |
| `Element.Separator` | 분리선 | Separator 컨테이너 내 |
| `Element.Marker` | 마커 | Marker 컨테이너 내 |
| `Element.Decoration` | 장식 | 겹침 시 Group 필수 |
| `Element.Background` | 배경 | **부모 내 1개만**, 겹침 불가 |

---

## 3. 핵심 규칙

### 3.1 계층 구조 규칙

```
Page (최상위)
  └── LayoutContainer (중간)
        └── Element (말단)
```

❌ **위반 예시:**
```
Page
  └── Element.Title  ← 잘못됨! LayoutContainer 없이 바로 Element
```

✅ **올바른 구조:**
```
Page
  └── LayoutContainer.Title
        └── Element.Title
```

### 3.2 Background 단일성 규칙 ⭐

**하나의 Group/HStack/VStack 내에 `Element.Background`는 반드시 1개만!**

❌ **위반:**
```
Group
├── Element.Background (배경1)
├── Element.Background (배경2)  ← 위반!
└── Element.Title
```

✅ **올바른 구조:**
```
Group
├── Element.Background (1개만!)
└── Element.Title
```

### 3.3 겹침(Overlap) 규칙 ⭐

**요소가 시각적으로 겹치면 반드시 Group으로 묶어야 함!**

겹침 판단 (position 사용):
```
두 요소 A, B가 겹침 = NOT (
  A가 B의 완전히 왼쪽 OR
  A가 B의 완전히 오른쪽 OR
  A가 B의 완전히 위 OR
  A가 B의 완전히 아래
)
```

❌ **위반 (겹치는데 같은 레벨):**
```
Group
├── Element.Background  ← position: (0,0, 500,500)
├── Element.Decoration  ← position: (100,100, 200,200) - 겹침!
└── Element.Title
```

✅ **올바른 구조:**
```
Group
├── Group (겹치는 것들 묶음)
│   ├── Element.Background
│   └── Element.Decoration
└── Element.Title
```

---

## 4. 처리 흐름

### 4.1 트리 순회 방식

**DFS (깊이 우선 탐색)으로 각 노드를 순회합니다.**

```
root (depth 0)
│
├──→ [처리 1] root 검증
│
├── child1 (depth 1)
│   │
│   ├──→ [처리 2] child1 검증
│   │
│   ├── grandchild1 (depth 2)
│   │   └──→ [처리 3] grandchild1 검증
│   │
│   └── grandchild2 (depth 2)
│       └──→ [처리 4] grandchild2 검증
│
└── child2 (depth 1)
    └──→ [처리 5] child2 검증
```

### 4.2 각 노드 처리 시 입력되는 정보

```python
validate_role(
    node,      # 현재 노드 (검증 대상)
    parent,    # 부모 노드 (1개)
    siblings,  # 형제 노드들 (오른쪽 형제만!)
    children   # 자식 노드들 (직접 자식만! 손자 X)
)
```

### 4.3 중요: "직접 자식"만 포함!

```
root의 children에 포함되는 것:
├── b5cd8702... ✅ (직접 자식)
├── group_header ✅ (직접 자식)
│   ├── group_header_bar ❌ (손자 - 포함 안됨!)
│   └── hstack_header_main ❌ (손자 - 포함 안됨!)
└── grid_cards ✅ (직접 자식)
```

**왜?** 각 레벨에서 "내 직접 자식들끼리" 검증해야 정확히 어디가 문제인지 알 수 있음!

---

## 5. 실제 데이터 예시

### 5.1 실제 트리 구조 (simplified_structure.json 기반)

```
root (Role.Page.Content, Group)
│
├── b5cd8702... (Element.Background, Image)
│   └── position: (0, 50, 1924, 1078)
│
├── group_header (LayoutContainer.Description, Group)
│   │
│   ├── group_header_bar (LayoutContainer.Background, Group)
│   │   │
│   │   ├── ba9095d1... (Element.Background, SVG)  ← ⚠️
│   │   │   └── position: (22, 137, 1770, 168)
│   │   │
│   │   └── c0679ad0... (Element.Background, Image) ← ⚠️ Background 2개!
│   │       └── position: (0, 0, 1814, 137)
│   │
│   └── hstack_header_main (LayoutContainer.Title, HStack)
│       ├── bb15e624... (Element.Title, Text) "Problem"
│       └── group_company_brand (LayoutContainer.Decoration, Group)
│           ├── 7316b050... (Element.Decoration, SVG)
│           └── fc0e15e3... (Element.Decoration, Text) "MIRICOMPANY"
│
├── group_section_intro_bg (LayoutContainer.Description, Group)
│   ├── de18958a... (Element.Background, SVG)
│   └── vstack_section_intro (LayoutContainer.Description, VStack)
│       ├── group_title_line (LayoutContainer.Title, Group)
│       │   ├── f68d4db4... (Element.Background, SVG) ← 겹침!
│       │   └── afa55edc... (Element.Title, Text) ← 겹침!
│       └── d624cd1a... (Element.Description, Text)
│
└── grid_cards (LayoutContainer.Description, HStack)
    ├── group_card1 → group_card1_icon (4개 요소 겹침!)
    ├── group_card2 → ...
    └── group_card3 → ...
```

### 5.2 문제 발견 지점

| 위치 | 문제 | 이유 |
|------|------|------|
| `group_header_bar` | Background 2개 | ba9095d1, c0679ad0 둘 다 Background |
| `group_card1_icon` | Decoration 겹침 | 4개 요소가 서로 겹침 |
| `group_title_line` | Background + Title 겹침 | 같은 Group 내 겹침 |

---

## 6. LLM 입출력 상세

### 6.1 입력 예시: group_header_bar 처리 시

**상황:** depth 2에서 `group_header_bar` 노드 검증

```json
{
  "node_info": {
    "id": "group_header_bar",
    "role": "Role.LayoutContainer.Background",
    "type": "Group",
    "position": {
      "x": 0,
      "y": 0,
      "width": 1813.65,
      "height": 304.73
    },
    "has_children": true,
    "children_count": 2
  },
  
  "parent_info": {
    "id": "group_header",
    "role": "Role.LayoutContainer.Description",
    "type": "Group"
  },
  
  "siblings_info": {
    "siblings": [
      {
        "id": "hstack_header_main",
        "role": "Role.LayoutContainer.Title",
        "type": "HStack",
        "position": {"x": 67, "y": 152, "width": 1733, "height": 120}
      }
    ],
    "same_role_count": 0,
    "total_siblings": 1
  },
  
  "children_info": {
    "children": [
      {
        "id": "ba9095d1-d5e1-4c94-976b-224abbb459f5",
        "role": "Role.Element.Background",
        "type": "SVG",
        "position": {"x": 21.62, "y": 136.89, "width": 1770.40, "height": 167.84}
      },
      {
        "id": "c0679ad0-9925-4329-bda7-f0109c982dc4",
        "role": "Role.Element.Background",
        "type": "Image",
        "position": {"x": 0, "y": 0, "width": 1813.65, "height": 136.89}
      }
    ],
    "role_distribution": {
      "Role.Element.Background": 2
    },
    "total_children": 2
  }
}
```

### 6.2 LLM 분석 과정

```
1. children 확인:
   - Role.Element.Background: 2개 ← 🚨 위반!
   
2. 겹침 확인:
   - 첫 번째: y=136.89 ~ y=304.73
   - 두 번째: y=0 ~ y=136.89
   - 겹치지 않음 (y 범위가 분리됨)
   
3. 판단:
   - 겹치든 안겹치든 Background는 1개만 허용
   - 위반 확정!
```

### 6.3 출력 예시

```json
{
  "is_valid": false,
  "current_role": "Role.LayoutContainer.Background",
  "issues": [
    {
      "type": "background_duplicate",
      "description": "자식 노드에 Element.Background가 2개 존재합니다 (ba9095d1..., c0679ad0...). Background는 부모당 1개만 허용됩니다.",
      "severity": "error"
    }
  ],
  "suggestions": [
    {
      "action": "change_role",
      "target_id": "c0679ad0-9925-4329-bda7-f0109c982dc4",
      "suggested_role": "Role.Element.Decoration",
      "reason": "위쪽 이미지(y=0~137)는 Decoration으로 변경하세요. 아래쪽 SVG(y=137~305)만 Background로 유지."
    }
  ],
  "confidence": 0.90,
  "reason": "Background 단일성 규칙 위반 - 부모 내 1개만 허용"
}
```

---

## 7. 문제 케이스와 해결

### 7.1 케이스: Background 중복 (겹치지 않음)

**현재 구조:**
```
group_header_bar (Group)
├── ba9095d1... (Background, SVG) - y: 137~305
└── c0679ad0... (Background, Image) - y: 0~137
```

**문제:** Background 2개 (겹치지 않아도 위반!)

**해결 방법 1: Decoration으로 변경**
```
group_header_bar (Group)
├── ba9095d1... (Background, SVG)  ← 유지
└── c0679ad0... (Decoration, Image) ← 변경!
```

**해결 방법 2: 각각 Group으로 분리**
```
group_header_bar (Group)
├── Group (상단 영역)
│   └── c0679ad0... (Background, Image)
└── Group (하단 영역)
    └── ba9095d1... (Background, SVG)
```

### 7.2 케이스: 겹치는 요소들 (group_card1_icon)

**현재 구조:**
```
group_card1_icon (Group)
├── 028bf193... (Background, SVG)   - position: (0,0, 467,483)
├── f3ceed2f... (Decoration, SVG)   - position: (147,47, 174,174) ← 겹침!
├── 3731f570... (Decoration, Image) - position: (194,95, 79,79)   ← 겹침!
└── 38181cb3... (Decoration, SVG)   - position: (481,183, 78,78)  ← 겹침!
```

**시각화:**
```
┌─────────────────────────────────────────┐
│  Background (0,0 ~ 467,483)              │
│   ┌───────────────┐                      │
│   │ Decoration 1  │   ○ Decoration 3     │
│   │  (원형 배경)   │     (오른쪽)         │
│   │ ┌─────────┐   │                      │
│   │ │ Deco 2  │   │                      │
│   │ │ (아이콘) │   │                      │
│   │ └─────────┘   │                      │
│   └───────────────┘                      │
└─────────────────────────────────────────┘
```

**문제:** 
- Background와 Decoration들이 겹침 (OK - Background 위에 Decoration)
- Decoration 1과 Decoration 2가 겹침 (❌ - Group으로 안 묶임!)

**해결:**
```
group_card1_icon (Group)
├── 028bf193... (Background, SVG)
├── Group (겹치는 장식들)
│   ├── f3ceed2f... (Background, SVG)   ← 이 Group 내 Background
│   └── 3731f570... (Decoration, Image) ← 이 Group 내 Decoration
└── 38181cb3... (Decoration, SVG)       ← 안 겹쳐서 그냥 나열 OK
```

### 7.3 케이스: 제목 + 배경 겹침 (group_title_line)

**현재 구조:**
```
group_title_line (LayoutContainer.Title, Group)
├── f68d4db4... (Background, SVG)  - position: (0, 1, 536, 46)
└── afa55edc... (Title, Text)      - position: (26, 0, 485, 49) ← 겹침!
```

**이건 올바른 구조!** 
- Background 위에 Title이 겹치는 건 정상
- 같은 Group 내에서 Background 1개 + Title 1개 = OK

**왜 OK?**
- Background는 1개만 ✅
- Title은 부모 내 1개만 ✅
- Background 위에 콘텐츠가 겹치는 건 의도된 디자인

---

## 8. 요약

### 검증 체크리스트

```
□ 계층 구조가 맞는가? (Page → Container → Element)
□ Background가 부모당 1개인가?
□ Title이 부모당 1개인가?
□ Decoration끼리 겹치면 Group으로 묶여있는가?
□ 겹치는 요소들이 적절히 Group으로 구조화되어 있는가?
```

### LLM 출력 형식

```json
{
  "is_valid": true/false,
  "current_role": "현재 Role",
  "issues": [
    {"type": "...", "description": "...", "severity": "error/warning/info"}
  ],
  "suggestions": [
    {"action": "change_role/wrap_with_group", "target_id": "...", "reason": "..."}
  ],
  "confidence": 0.0~1.0,
  "reason": "요약"
}
```

### Issue Types

| Type | 설명 |
|------|------|
| `hierarchy_error` | 계층 구조 위반 |
| `constraint_violation` | 제약 조건 위반 (Title 2개 등) |
| `background_duplicate` | Background 중복 |
| `decoration_overlap` | Decoration 겹침 (Group 필요) |
| `semantic_mismatch` | Role과 콘텐츠 불일치 |
| `layout_type_error` | Layout Type 부적절 |

### Suggestion Actions

| Action | 설명 |
|--------|------|
| `change_role` | Role 변경 |
| `wrap_with_group` | Group으로 묶기 |
| `add_element` | 요소 추가 |
| `remove_element` | 요소 제거 |
| `change_type` | Type 변경 |
