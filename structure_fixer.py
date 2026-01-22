#!/usr/bin/env python3
"""
Structure Fixer: RLSC 구조의 규칙 위반을 LLM으로 수정

파이프라인:
1. 입력 (상대좌표) → 절대좌표 변환
2. LLM (GPT-4.1) + 이미지 + 규칙 → 구조 수정
3. 출력 (절대좌표) → 상대좌표 변환 (룰베이스)
4. padding/gap 계산 (add_layout_properties)

규칙:
- Background: 그룹당 1개만, 겹침 허용
- Decoration: 겹침 불허 → 겹치면 Group으로 묶고 Background로 변경 등
- Background가 2개면 → 묶거나 속성 변경
"""

import json
import base64
import os
from pathlib import Path
from typing import Dict, Optional
from copy import deepcopy

# ============================================================
# 🔧 설정 변수
# ============================================================

INPUT_STRUCTURE = "data/286622/structure_json.json"
INPUT_IMAGE = "data/286622/thumbnail.png"
OUTPUT_FILE = "data/286622/structure_fixed.json"

# ============================================================
# 좌표 변환 함수
# ============================================================

def to_absolute_coords(node: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> Dict:
    """
    상대좌표 → 절대좌표 변환 (재귀)
    """
    result = {}
    
    # 기본 속성 복사
    for key, value in node.items():
        if key not in ('position', 'children'):
            result[key] = deepcopy(value)
    
    # position 절대좌표로 변환
    pos = node.get('position', {})
    if pos:
        abs_x = parent_abs_x + pos.get('x', 0)
        abs_y = parent_abs_y + pos.get('y', 0)
        result['position'] = {
            'x': round(abs_x, 2),
            'y': round(abs_y, 2),
            'width': round(pos.get('width', 0), 2),
            'height': round(pos.get('height', 0), 2)
        }
    else:
        abs_x = parent_abs_x
        abs_y = parent_abs_y
        result['position'] = {'x': 0, 'y': 0, 'width': 0, 'height': 0}
    
    # 자식들 재귀 변환
    children = node.get('children', [])
    if children:
        result['children'] = [
            to_absolute_coords(child, abs_x, abs_y)
            for child in children
        ]
    
    return result


def to_relative_coords(node: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> Dict:
    """
    절대좌표 → 상대좌표 변환 (재귀)
    """
    result = {}
    
    # 기본 속성 복사
    for key, value in node.items():
        if key not in ('position', 'children'):
            result[key] = deepcopy(value)
    
    # position 상대좌표로 변환
    pos = node.get('position', {})
    abs_x = pos.get('x', 0)
    abs_y = pos.get('y', 0)
    
    result['position'] = {
        'x': round(abs_x - parent_abs_x, 2),
        'y': round(abs_y - parent_abs_y, 2),
        'width': round(pos.get('width', 0), 2),
        'height': round(pos.get('height', 0), 2)
    }
    
    # 자식들 재귀 변환
    children = node.get('children', [])
    if children:
        result['children'] = [
            to_relative_coords(child, abs_x, abs_y)
            for child in children
        ]
    
    return result


# ============================================================
# 이미지 인코딩
# ============================================================

def encode_image(image_path: str) -> Optional[str]:
    """이미지를 base64로 인코딩"""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"❌ 이미지 로드 실패: {e}")
        return None


# ============================================================
# 프롬프트 생성
# ============================================================

def create_fix_prompt(structure_json: str) -> str:
    """LLM 프롬프트 생성"""
    return f"""## 작업: RLSC 구조 수정

당신은 UI 레이아웃 구조 전문가입니다. 
이미지와 아래 JSON 구조를 보고, 규칙 위반을 수정해주세요.

---

### ⭐ 핵심 규칙

#### 1. Background 규칙
- **각 컨테이너(Group/ZStack/HStack/VStack)에 Background는 1개만**
- Background는 다른 요소와 **겹침 허용**
- Background는 보통 가장 크고, 다른 요소들 뒤에 있음
- `role: "Role.Element.Background"`

#### 2. Decoration 규칙  
- Decoration끼리는 **겹침 불허**
- 겹치면 → **Group/ZStack으로 묶고** 큰 것을 **Background로 role 변경**
- `role: "Role.Element.Decoration"`

#### 3. 컨테이너 타입 선택 (이미지 보고 판단)
- **HStack**: 요소들이 가로로 나열
- **VStack**: 요소들이 세로로 나열
- **ZStack**: 요소들이 의도적으로 겹침 (레이어링)
- **Group**: 불규칙 배치

#### 4. 위반 케이스 처리
| 위반 | 해결 방법 |
|------|-----------|
| Background 2개 이상 | 하나만 Background, 나머지는 Decoration으로 변경 |
| Decoration끼리 겹침 | Group으로 묶고 큰 것을 Background로 변경 |
| 원형배경 + 아이콘 | LayoutContainer.Marker로 묶기 (원형=Background, 아이콘=Marker) |

---

### 📊 현재 구조 (절대좌표)

아래 JSON의 position은 **절대좌표**입니다.
이미지와 좌표를 대조하여 각 요소의 위치를 파악하세요.

```json
{structure_json}
```

---

### 📤 출력 요구사항

1. 수정된 전체 JSON 구조를 **절대좌표 그대로** 반환
2. 기존 요소의 `id`, `position`은 **최대한 유지**
3. 필요시 새 그룹 노드 생성 (새 id 부여)
4. role 변경이 필요하면 변경
5. JSON만 출력 (```json 블록 사용)"""


# ============================================================
# LLM 호출
# ============================================================

def call_llm(prompt: str, image_base64: Optional[str], image_path: str) -> Optional[str]:
    """GPT-4.1 호출"""
    try:
        from openai import OpenAI
        
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            print("❌ OPENAI_API_KEY 환경변수가 없습니다.")
            return None
        
        client = OpenAI(api_key=api_key)
        
        # 이미지 MIME 타입
        suffix = Path(image_path).suffix.lower()
        mime_type = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}.get(suffix[1:], 'image/png')
        
        # 메시지 구성
        if image_base64:
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_base64}",
                        "detail": "high"
                    }
                },
                {"type": "text", "text": prompt}
            ]
        else:
            user_content = prompt
        
        print("🤖 GPT-4.1 호출 중...")
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "system",
                    "content": """당신은 UI 레이아웃 구조 전문가입니다.
이미지와 JSON 구조를 분석하여 규칙 위반을 수정합니다.
JSON만 출력하세요."""
                },
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=8000
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        print(f"❌ LLM 호출 실패: {e}")
        return None


def parse_json_response(response: str) -> Optional[Dict]:
    """LLM 응답에서 JSON 파싱"""
    try:
        # JSON 블록 추출
        if '```json' in response:
            start = response.find('```json') + 7
            end = response.find('```', start)
            json_str = response[start:end].strip()
        elif '```' in response:
            start = response.find('```') + 3
            end = response.find('```', start)
            json_str = response[start:end].strip()
        else:
            json_str = response.strip()
        
        return json.loads(json_str)
    except Exception as e:
        print(f"❌ JSON 파싱 실패: {e}")
        return None


# ============================================================
# padding/gap 계산 (add_layout_properties에서 가져옴)
# ============================================================

def add_layout_properties(node: Dict) -> Dict:
    """padding, gap, direction 속성 추가 (룰베이스)"""
    node_type = node.get('type', '')
    children = node.get('children', [])
    position = node.get('position', {})
    
    # Stack 타입인 경우에만 처리
    if node_type in ('HStack', 'VStack', 'ZStack', 'Group'):
        # direction 설정
        if node_type == 'HStack':
            node['direction'] = 'horizontal'
        elif node_type == 'VStack':
            node['direction'] = 'vertical'
        
        if children and position:
            parent_width = position.get('width', 0)
            parent_height = position.get('height', 0)
            
            # 자식들의 위치 정보 수집
            child_positions = []
            for child in children:
                child_pos = child.get('position', {})
                if child_pos:
                    child_positions.append({
                        'x': child_pos.get('x', 0),
                        'y': child_pos.get('y', 0),
                        'width': child_pos.get('width', 0),
                        'height': child_pos.get('height', 0)
                    })
            
            if child_positions:
                # Padding 계산
                min_x = min(cp['x'] for cp in child_positions)
                min_y = min(cp['y'] for cp in child_positions)
                max_right = max(cp['x'] + cp['width'] for cp in child_positions)
                max_bottom = max(cp['y'] + cp['height'] for cp in child_positions)
                
                node['padding'] = {
                    'top': round(min_y, 2),
                    'bottom': round(max(0, parent_height - max_bottom), 2),
                    'left': round(min_x, 2),
                    'right': round(max(0, parent_width - max_right), 2)
                }
                
                # Gap 계산 (자식이 2개 이상일 때만)
                if len(child_positions) >= 2 and node_type in ('HStack', 'VStack'):
                    gaps = []
                    direction = node.get('direction', 'vertical')
                    
                    if direction == 'horizontal':
                        sorted_children = sorted(child_positions, key=lambda c: c['x'])
                        for i in range(len(sorted_children) - 1):
                            curr = sorted_children[i]
                            next_ = sorted_children[i + 1]
                            gap = next_['x'] - (curr['x'] + curr['width'])
                            gaps.append(gap)
                    else:
                        sorted_children = sorted(child_positions, key=lambda c: c['y'])
                        for i in range(len(sorted_children) - 1):
                            curr = sorted_children[i]
                            next_ = sorted_children[i + 1]
                            gap = next_['y'] - (curr['y'] + curr['height'])
                            gaps.append(gap)
                    
                    if gaps:
                        avg_gap = sum(max(0, g) for g in gaps) / len(gaps)
                        node['gap'] = round(avg_gap, 2)
                    else:
                        node['gap'] = 0
                else:
                    node['gap'] = 0
    
    # 자식 노드들도 재귀적으로 처리
    if children:
        for child in children:
            add_layout_properties(child)
    
    return node


# ============================================================
# 메인 파이프라인
# ============================================================

def main():
    base_path = Path(__file__).parent
    
    input_path = base_path / INPUT_STRUCTURE
    image_path = base_path / INPUT_IMAGE
    output_path = base_path / OUTPUT_FILE
    
    print("\n" + "=" * 60)
    print("🔧 Structure Fixer")
    print("=" * 60)
    
    # 파일 확인
    if not input_path.exists():
        print(f"❌ 입력 파일 없음: {input_path}")
        return
    
    print(f"\n📋 설정:")
    print(f"   - 입력: {input_path}")
    print(f"   - 이미지: {image_path}")
    print(f"   - 출력: {output_path}")
    
    # 1. 입력 로드
    print("\n📥 Step 1: 입력 로드")
    with open(input_path, 'r', encoding='utf-8') as f:
        structure = json.load(f)
    print(f"   ✅ 로드 완료")
    
    # 2. 절대좌표 변환
    print("\n🔄 Step 2: 절대좌표 변환")
    structure_abs = to_absolute_coords(structure)
    print(f"   ✅ 변환 완료")
    
    # 3. 이미지 인코딩
    print("\n🖼️ Step 3: 이미지 인코딩")
    image_base64 = encode_image(str(image_path)) if image_path.exists() else None
    if image_base64:
        print(f"   ✅ 인코딩 완료")
    else:
        print(f"   ⚠️ 이미지 없음 (텍스트만 사용)")
    
    # 4. LLM 호출
    print("\n🤖 Step 4: LLM 구조 수정")
    structure_json = json.dumps(structure_abs, ensure_ascii=False, indent=2)
    prompt = create_fix_prompt(structure_json)
    
    response = call_llm(prompt, image_base64, str(image_path))
    
    if not response:
        print("   ❌ LLM 응답 없음, 원본 유지")
        fixed_abs = structure_abs
    else:
        fixed_abs = parse_json_response(response)
        if not fixed_abs:
            print("   ❌ JSON 파싱 실패, 원본 유지")
            fixed_abs = structure_abs
        else:
            print(f"   ✅ 수정 완료")
    
    # 5. 상대좌표 변환 (룰베이스)
    print("\n🔄 Step 5: 상대좌표 변환")
    fixed_rel = to_relative_coords(fixed_abs)
    print(f"   ✅ 변환 완료")
    
    # 6. padding/gap 계산 (룰베이스)
    print("\n📐 Step 6: padding/gap 계산")
    result = add_layout_properties(fixed_rel)
    print(f"   ✅ 계산 완료")
    
    # 7. 저장
    print("\n💾 Step 7: 결과 저장")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 저장 완료: {output_path}")
    
    print("\n" + "=" * 60)
    print("🎉 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
