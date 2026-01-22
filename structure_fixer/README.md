# Structure Fixer

RLSC 구조의 규칙 위반을 LLM(GPT-4.1)으로 수정하는 파이프라인

## 📁 폴더 구조

```
structure_fixer/
├── run.py                    # 메인 실행 스크립트
├── README.md                 # 이 문서
├── prompts/
│   ├── fix_rules.yaml        # 수정 규칙 및 프롬프트 템플릿
│   └── examples.yaml         # Few-shot 예시
└── samples/
    ├── sample_286622.json    # 샘플 입력 JSON
    ├── sample_286622.png     # 샘플 이미지
    ├── sample_277987.json    # 샘플 입력 JSON
    └── sample_277987.png     # 샘플 이미지
```

## 🚀 실행 방법

```bash
cd structure_fixer
export OPENAI_API_KEY="your-api-key"
python run.py
```

## ⚙️ 설정 변경

`run.py` 상단의 변수만 수정:

```python
SAMPLE_NAME = "sample_286622"  # 또는 sample_277987
```

## 🔄 파이프라인

| 단계 | 설명 | 방식 |
|------|------|------|
| Step 1 | 프롬프트 YAML 로드 | 파일 |
| Step 2 | 입력 JSON 로드 | 파일 |
| Step 3 | 상대좌표 → 절대좌표 | 룰베이스 |
| Step 4 | 이미지 Base64 인코딩 | 룰베이스 |
| Step 5 | GPT-4.1 호출 (이미지+구조+규칙) | **LLM** |
| Step 6 | 절대좌표 → 상대좌표 | 룰베이스 |
| Step 7 | padding/gap 계산 | 룰베이스 |
| Step 8 | 결과 저장 | 파일 |

## 📋 핵심 규칙

### Background
- 컨테이너(Group/ZStack/HStack/VStack)당 **1개만**
- 다른 요소와 **겹침 허용**
- `role: "Role.Element.Background"`

### Decoration
- **겹침 불허**
- 겹치면 → Group으로 묶고 큰 것을 Background로 변경
- `role: "Role.Element.Decoration"`

### 컨테이너 타입
- **HStack**: 가로 배열
- **VStack**: 세로 배열
- **ZStack**: 레이어링 (겹침)
- **Group**: 불규칙 배치

## 📝 프롬프트 커스터마이징

`prompts/fix_rules.yaml` 수정:

```yaml
system_prompt: |
  당신은 UI 레이아웃 구조 전문가입니다.
  ...

user_prompt_template: |
  ## 작업: RLSC 구조 수정
  ...
  {structure_json}  # ← 구조 JSON이 삽입되는 위치
  ...
```

## 💡 출력 예시

입력: `sample_286622.json`
출력: `sample_286622_fixed.json`

결과 파일에는 수정된 구조 + padding/gap이 포함됩니다.
