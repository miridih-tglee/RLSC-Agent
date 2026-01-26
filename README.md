# Design Object 분석 및 처리 도구

Design Object의 구조 분석, 유효성 검사, 스마트블록 적합성 판단을 위한 Python 도구 모음입니다.

## 개요

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Design Object 처리 파이프라인                      │
├─────────────────────────────────────────────────────────────────────┤
│  1. count_valid_containers.py     → ZStack/Group 유효성 분석         │
│  2. check_smartblock_rulebased.py → 스마트블록 적합성 판단           │
│  3. find_fix_candidates.py        → 구조 수정 대상 찾기              │
│  4. process_design_object.py      → 구조 수정 및 파일 저장           │
│  5. update_structure_json_fixed.py → 수정된 구조 DB 업데이트         │
└─────────────────────────────────────────────────────────────────────┘
```

## 설치

```bash
pip install -r requirements.txt
```

### 의존성
- `psycopg2-binary`: PostgreSQL DB 연결
- `httpx`: HTTP 요청 (썸네일 다운로드)
- `Pillow`: 이미지 처리

---

## 1. count_valid_containers.py

### 개요
DB에서 design_objects를 조회하여 **ZStack/Group 컨테이너의 유효성**을 검사합니다.

### 유효 조건
ZStack/Group의 자식이 다음 조건을 만족해야 유효:

| 조건 | 구성 |
|------|------|
| 조건 1 | Background(SVG/Image) + VStack 1개 |
| 조건 2 | Background(SVG/Image) + HStack 1개 |
| 조건 3 | Background(SVG/Image) + Element 1개 |

**Element roles**: Title, Subtitle, Highlight, Description, Separator, Marker, Decoration

### 분류 기준
- **valid**: 모든 ZStack/Group이 유효
- **invalid**: 하나라도 유효하지 않은 ZStack/Group이 있음
- **no_container**: ZStack/Group이 없음

### 필터링 옵션
- `depth 4~8`: 구조 깊이 제한
- `Page* 제외`: Role.LayoutContainer.Page 패턴 제외
- `Grid/Graph 포함 제외`: Grid, Graph 타입 포함 시 제외
- `Frame 겹침 제외`: Frame과 다른 요소가 겹치면 제외 (옵션)

### 사용법

```bash
python count_valid_containers.py
```

### 출력 파일
| 파일 | 설명 |
|------|------|
| `data/valid_containers.json` | 분석 결과 요약 + 샘플 |
| `data/valid_container_ids.json` | valid ID 목록 |
| `data/invalid_container_ids.json` | invalid ID 목록 |
| `data/valid_composition_ids.json` | valid 조합별 ID 목록 |
| `data/invalid_composition_ids.json` | invalid 조합별 ID 목록 |
| `data/valid_compositions_summary.csv` | valid 조합 요약 CSV |
| `data/invalid_compositions_summary.csv` | invalid 조합 요약 CSV |

### 설정 변경
```python
# count_valid_containers.py 상단
BATCH_SIZE = 5000           # 배치 크기
NUM_WORKERS = cpu_count()-1 # 병렬 워커 수
MIN_DEPTH = 4               # 최소 깊이
MAX_DEPTH = 8               # 최대 깊이
EXCLUDE_FRAME_OVERLAP = True  # Frame 겹침 제외 여부
```

---

## 2. check_smartblock_rulebased.py

### 개요
`content_signature`를 분석하여 **LLM 없이** 스마트블록 적합성을 판단합니다.

### 핵심 로직
1. 컨테이너(Grid, HStack, VStack, ZStack, Group)의 children이 2개 이상인지 확인
2. children의 구조적 시그니처가 동일하거나 유사한지 비교
3. 동일/유사한 구조가 반복되면 스마트블록 적합

### 매칭 타입
| 타입 | 아이콘 | 설명 |
|------|--------|------|
| exact | `=` | 완전 일치 (구조 시그니처 동일) |
| skeleton | `≈` | 스켈레톤 일치 (중복 요소 무시) |
| similar | `~` | 유사도 기반 (70% 이상 유사) |

### 패턴 분류
| 패턴 | 조건 |
|------|------|
| 팀원/프로필 카드 | Image/Frame + Title + Description |
| 이미지+텍스트 카드 | Image/Frame + Title |
| 아이콘+텍스트 카드 | SVG + Title + Description |
| 아이콘+제목 리스트 | SVG + Title |
| 정보 카드 그리드 | VStack/HStack + Title + Description |

### 사용법

```bash
# 폴더 기반 분석 (content_signature.json 파일 필요)
python check_smartblock_rulebased.py --dir ./samples

# DB 기반 분석 (JSON 파일에서 ID 목록 읽기)
python check_smartblock_rulebased.py --json data/valid_container_ids.json

# 개수 제한
python check_smartblock_rulebased.py --json data/valid_container_ids.json --limit 100

# CSV 결과 저장
python check_smartblock_rulebased.py --json data/valid_container_ids.json --save-csv

# 상세 출력
python check_smartblock_rulebased.py --json data/valid_container_ids.json --verbose

# 최소 leaf 노드 수 설정 (작은 라벨 그룹 필터링)
python check_smartblock_rulebased.py --json data/valid_container_ids.json --min-leaf 5
```

### CLI 옵션
| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--dir <경로>` | 분석할 디렉토리 (폴더 기반) | `./negative_samples` |
| `--json <경로>` | ID JSON 파일 (DB 기반) | - |
| `--folder <이름>` | 특정 폴더만 분석 | - |
| `--limit <N>` | 분석할 개수 제한 | 전체 |
| `--output <경로>` | 전체 결과 JSON 저장 | - |
| `--output-dir <경로>` | CSV 결과 저장 디렉토리 | `./data` |
| `--verbose` | 상세 출력 | 비활성화 |
| `--save-csv` | CSV 결과 저장 | 비활성화 |
| `--min-leaf <N>` | 최소 leaf 노드 수 | 3 |

### 출력 파일 (--save-csv 사용 시)
| 파일 | 설명 |
|------|------|
| `data/sm_valid.csv` | 적합 판정 결과 |
| `data/sm_invalid.csv` | 부적합 판정 결과 |

### 점수 계산
- 반복 횟수: 2개(+2), 3개(+3), 4개+(+4)
- 반복 비율: 100%(+3), 80%+(+2), 50%+(+1)
- Grid 컨테이너: +1
- 의미있는 패턴: +1
- **5점 이상 + 2개 이상 반복 시 적합 판정**

---

## 3. process_design_object.py

### 개요
DB에서 design_object 데이터를 가져와 **구조 수정 및 파일 저장**을 수행합니다.

### 처리 파이프라인

```
┌─────────────────────────────────────────────────────────────────────┐
│  📥 Step 1: DB에서 데이터 조회                                        │
│  📷 Step 2: 썸네일 다운로드 (WebP → PNG, 투명→흰색 배경)               │
│  📄 Step 3: 원본 파일 저장                                            │
│  🔧 Step 4: Structure 수정                                           │
│       ├─ Frame/Image → Marker 변환                                   │
│       ├─ 절대좌표 변환                                                │
│       ├─ 겹침 수정 (Background 승격, Group 묶기)                       │
│       ├─ 상대좌표 변환                                                │
│       └─ padding/gap/direction 추가                                  │
│  💾 Step 5: 수정된 Structure 저장                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 구조 수정 규칙

#### Frame/Image → Marker 변환
- `type: Frame` → `role: Role.Element.Marker`
- Frame 안의 Image → `role: Role.Element.Marker`
- 단독 Image → `role: Role.Element.Marker`

#### 겹침 수정
1. **Background 승격**: Text와 겹치는 가장 큰 Decoration(SVG)을 Background로
2. **Group 묶기**: Decoration/Marker끼리 겹치면 Group으로 묶음
3. **Background 중복 제거**: 여러 Background 중 가장 큰 것만 유지

#### Layout 속성 추가
- `direction`: HStack→horizontal, VStack→vertical
- `padding`: 자식들의 bounding box로 계산
- `gap`: 인접 자식들 간 간격 평균

### 사용법

```bash
# 단일 ID 처리
python process_design_object.py 283782

# 여러 ID (쉼표 또는 공백으로 구분)
python process_design_object.py 283782,283725,277457
python process_design_object.py 283782 283725 277457

# 폴더 경로 (폴더 내 디렉토리명을 ID로 사용)
python process_design_object.py --dir /path/to/folder

# 출력 폴더 지정
python process_design_object.py --dir /path/to/folder --output /path/to/output

# 상세 로그 출력
python process_design_object.py --dir /path/to/folder -v
```

### CLI 옵션
| 옵션 | 설명 |
|------|------|
| `<ids>` | 처리할 디자인 오브젝트 ID (쉼표/공백 구분) |
| `--dir, -d <경로>` | ID를 추출할 디렉토리 경로 |
| `--output, -o <경로>` | 출력 디렉토리 경로 |
| `--verbose, -v` | 상세 로그 출력 |

### 출력 파일 (ID별 폴더)
| 파일 | 설명 |
|------|------|
| `thumbnail.png` | 썸네일 이미지 (PNG 변환) |
| `structure_json.json` | 원본 구조 |
| `structure_json_fixed.json` | 수정된 구조 |
| `content_signature.json` | 콘텐츠 시그니처 |
| `content_signature_sorted.txt` | 정렬된 시그니처 |
| `design_object_meta.json` | 메타데이터 |
| `info.json` | UUID, ID 정보 |

---

## 4. find_fix_candidates.py

### 개요
DB에서 `inference_model_type='agentic'`인 design_objects를 분석하여 **구조 수정이 필요한 항목**들을 찾습니다.

### 변경 대상 조건
1. **Background 중복**: 같은 컨테이너에 Background가 2개 이상
2. **요소 겹침**: Decoration/Marker가 서로 겹침

### 필터링 조건
- `max_depth`: 4 ~ 8
- 제외 `design_object_role`: Opening, Agenda, SectionDivider, Ending, Content
- 제외 `structure_json` role 패턴: `Role.LayoutContainer.Page*`

### 사용법

```bash
# 기본 실행 (단순 겹침 검사)
python find_fix_candidates.py

# 작은 박스 대비 비율로 겹침 검사 (더 엄격)
python find_fix_candidates.py --use-ratio

# 겹침 임계값 설정
python find_fix_candidates.py --threshold 0.1

# 출력 파일 지정
python find_fix_candidates.py --output data/my_candidates.json
```

### CLI 옵션
| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--use-ratio` | 작은 박스 대비 비율로 겹침 검사 | 비활성화 (단순 겹침) |
| `--threshold <N>` | 겹침 임계값 | use-ratio 시 0.1, 아니면 0.0 |
| `--output <경로>` | 출력 파일 경로 | `data/fix_candidates.json` |

### 출력 파일
| 파일 | 설명 |
|------|------|
| `data/fix_candidates.json` | 수정 대상 목록 + 이슈 상세 |

### 출력 JSON 구조
```json
{
  "metadata": {
    "statistics": {
      "total_db_filtered": 50000,
      "page_role_skipped": 5000,
      "needs_fix_count": 3000,
      "issue_type_counts": {
        "multiple_backgrounds": 500,
        "overlapping_decorations": 2800
      }
    }
  },
  "candidates": [
    {
      "id": 283782,
      "layout_id": 12345,
      "analysis": {
        "issue_count": 2,
        "issue_types": ["overlapping_decorations"]
      }
    }
  ]
}
```

### 설정 변경
```python
# find_fix_candidates.py 상단
BATCH_SIZE = 5000           # 배치 크기
NUM_WORKERS = cpu_count()-1 # 병렬 워커 수
MIN_DEPTH = 4               # 최소 깊이
MAX_DEPTH = 8               # 최대 깊이
```

---

## 5. update_structure_json_fixed.py

### 개요
폴더 내의 `structure_json_fixed.json` 파일을 읽어서 DB의 `structure_json_fixed` 컬럼에 업데이트합니다.

### 처리 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. 폴더에서 structure_json_fixed.json 파일 로드                      │
│  2. DB design_objects 테이블의 structure_json_fixed 컬럼 업데이트     │
└─────────────────────────────────────────────────────────────────────┘
```

### 사용법

```bash
# 단일 ID 업데이트
python update_structure_json_fixed.py 283782

# 여러 ID 업데이트
python update_structure_json_fixed.py 283782,283725,277457

# 특정 디렉토리의 모든 폴더 업데이트
python update_structure_json_fixed.py --dir /path/to/data

# dry-run (실제 업데이트 없이 확인만)
python update_structure_json_fixed.py --dir ./data --dry-run

# 컬럼이 없으면 생성
python update_structure_json_fixed.py --create-column
```

### CLI 옵션
| 옵션 | 설명 |
|------|------|
| `<ids>` | 업데이트할 디자인 오브젝트 ID (쉼표/공백 구분) |
| `--dir, -d <경로>` | 데이터 디렉토리 경로 |
| `--dry-run` | 실제 업데이트 없이 확인만 |
| `--create-column` | `structure_json_fixed` 컬럼이 없으면 생성 |

### 주의사항
- DB에 `structure_json_fixed` 컬럼이 없으면 `--create-column` 옵션으로 먼저 생성해야 합니다.
- `--dry-run` 옵션으로 먼저 확인 후 실제 업데이트를 권장합니다.

---

## DB 설정

모든 스크립트는 동일한 DB 설정을 사용합니다:

```python
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54322,
    "user": "postgres",
    "password": "postgres",
    "dbname": "postgres"
}
```

필요에 따라 각 스크립트 상단의 `DB_CONFIG`를 수정하세요.

---

## 프로젝트 구조

```
tg/
├── count_valid_containers.py       # ZStack/Group 유효성 분석
├── check_smartblock_rulebased.py   # 스마트블록 적합성 판단
├── find_fix_candidates.py          # 구조 수정 대상 찾기
├── process_design_object.py        # 구조 수정 및 파일 저장
├── update_structure_json_fixed.py  # 수정된 구조 DB 업데이트
│
├── data/                           # 결과 데이터
│   ├── valid_containers.json
│   ├── valid_container_ids.json
│   ├── invalid_container_ids.json
│   ├── fix_candidates.json
│   ├── sm_valid.csv
│   ├── sm_invalid.csv
│   └── ...
│
├── negative_samples/               # 샘플 데이터 (ID별 폴더)
│   └── {id}/
│       ├── thumbnail.png
│       ├── structure_json.json
│       ├── structure_json_fixed.json
│       ├── content_signature.json
│       └── ...
│
└── requirements.txt
```

---

## 일반적인 워크플로우

### 워크플로우 A: 스마트블록 적합성 판단

```bash
# 1. ZStack/Group 유효성 분석 → valid_container_ids.json 생성
python count_valid_containers.py

# 2. valid ID들에 대해 스마트블록 적합성 판단
python check_smartblock_rulebased.py --json data/valid_container_ids.json --save-csv
```

### 워크플로우 B: 구조 수정 및 DB 업데이트

```bash
# 1. 구조 수정이 필요한 항목 찾기 → fix_candidates.json 생성
python find_fix_candidates.py

# 2. 대상 ID들의 데이터 다운로드 및 구조 수정
python process_design_object.py 283782,283725,277457 --output ./data

# 3. 수정된 구조를 DB에 업데이트 (dry-run으로 먼저 확인)
python update_structure_json_fixed.py --dir ./data --dry-run

# 4. 실제 DB 업데이트
python update_structure_json_fixed.py --dir ./data
```

---

## 라이선스

Internal use only.
