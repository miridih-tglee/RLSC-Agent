#!/usr/bin/env python3
"""
Structure Fixer: RLSC 구조의 규칙 위반을 LLM으로 수정

파이프라인:
1. 입력 (상대좌표) → 절대좌표 변환
2. LLM (GPT-4.1) + 이미지 + 규칙 → 구조 수정 (병렬 처리)
3. 출력 (절대좌표) → 상대좌표 변환 (룰베이스)
4. padding/gap 계산 (add_layout_properties)

사용법:
  cd structure_fixer
  export OPENAI_API_KEY="your-key"
  python run.py
"""

import json
import base64
import os
import yaml
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from copy import deepcopy

# ============================================================
# 🔧 설정 변수 (여기만 수정하세요)
# ============================================================

# 샘플 선택 (samples 폴더 기준)
SAMPLE_NAME = "sample_277987"  # sample_286622 또는 sample_277987

# 병렬 처리 설정
PARALLEL_MODE = True   # True: 자식 노드별 병렬 처리, False: 단일 호출
MAX_WORKERS = 4        # 동시 LLM 호출 수

# 자동 설정 (수정 불필요)
BASE_DIR = Path(__file__).parent
INPUT_STRUCTURE = BASE_DIR / "samples" / f"{SAMPLE_NAME}.json"
INPUT_IMAGE = BASE_DIR / "samples" / f"{SAMPLE_NAME}.png"
OUTPUT_FILE = BASE_DIR / "samples" / f"{SAMPLE_NAME}_fixed.json"
PROMPTS_DIR = BASE_DIR / "prompts"

# ============================================================
# 프롬프트 로더
# ============================================================

def load_prompts() -> Dict:
    """YAML 프롬프트 파일 로드"""
    rules_path = PROMPTS_DIR / "fix_rules.yaml"
    examples_path = PROMPTS_DIR / "examples.yaml"
    
    prompts = {}
    
    if rules_path.exists():
        with open(rules_path, 'r', encoding='utf-8') as f:
            prompts['rules'] = yaml.safe_load(f)
    
    if examples_path.exists():
        with open(examples_path, 'r', encoding='utf-8') as f:
            prompts['examples'] = yaml.safe_load(f)
    
    return prompts


# ============================================================
# 좌표 변환 함수
# ============================================================

def to_absolute_coords(node: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> Dict:
    """상대좌표 → 절대좌표 변환 (재귀)"""
    result = {}
    
    for key, value in node.items():
        if key not in ('position', 'children'):
            result[key] = deepcopy(value)
    
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
    
    children = node.get('children', [])
    if children:
        result['children'] = [
            to_absolute_coords(child, abs_x, abs_y)
            for child in children
        ]
    
    return result


def to_relative_coords(node: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> Dict:
    """절대좌표 → 상대좌표 변환 (재귀)"""
    result = {}
    
    for key, value in node.items():
        if key not in ('position', 'children'):
            result[key] = deepcopy(value)
    
    pos = node.get('position', {})
    abs_x = pos.get('x', 0)
    abs_y = pos.get('y', 0)
    
    result['position'] = {
        'x': round(abs_x - parent_abs_x, 2),
        'y': round(abs_y - parent_abs_y, 2),
        'width': round(pos.get('width', 0), 2),
        'height': round(pos.get('height', 0), 2)
    }
    
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

def encode_image(image_path: Path) -> Optional[str]:
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

def create_fix_prompt(structure_json: str, prompts: Dict) -> str:
    """YAML 템플릿 기반 프롬프트 생성"""
    rules = prompts.get('rules', {})
    template = rules.get('user_prompt_template', '')
    
    if not template:
        raise ValueError("❌ prompts/fix_rules.yaml에 user_prompt_template이 없습니다!")
    
    return template.replace('{structure_json}', structure_json)


def get_system_prompt(prompts: Dict) -> str:
    """시스템 프롬프트 가져오기"""
    rules = prompts.get('rules', {})
    system_prompt = rules.get('system_prompt', '')
    
    if not system_prompt:
        raise ValueError("❌ prompts/fix_rules.yaml에 system_prompt가 없습니다!")
    
    return system_prompt


# ============================================================
# LLM 호출
# ============================================================

def call_llm_single(prompt: str, image_base64: Optional[str], image_path: Path, prompts: Dict, node_id: str = "") -> Optional[str]:
    """GPT-4.1 단일 호출"""
    try:
        from openai import OpenAI
        
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            print(f"❌ [{node_id}] OPENAI_API_KEY 환경변수가 없습니다.")
            return None
        
        client = OpenAI(api_key=api_key)
        
        suffix = image_path.suffix.lower() if image_path else '.png'
        mime_type = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(suffix, 'image/png')
        
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
        
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": get_system_prompt(prompts)},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=4000
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        print(f"❌ [{node_id}] LLM 호출 실패: {e}")
        return None


def parse_json_response(response: str) -> Optional[Dict]:
    """LLM 응답에서 JSON 파싱"""
    try:
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
# 병렬 처리
# ============================================================

def process_child_node(args: Tuple) -> Tuple[int, Optional[Dict]]:
    """단일 자식 노드 처리 (병렬 실행용)"""
    idx, child_node, image_base64, image_path, prompts = args
    
    node_id = child_node.get('id', f'child_{idx}')
    print(f"   🔄 [{node_id}] 처리 중...")
    
    # 프롬프트 생성
    structure_json = json.dumps(child_node, ensure_ascii=False, indent=2)
    prompt = create_fix_prompt(structure_json, prompts)
    
    # LLM 호출
    response = call_llm_single(prompt, image_base64, image_path, prompts, node_id)
    
    if not response:
        print(f"   ⚠️ [{node_id}] 응답 없음, 원본 유지")
        return (idx, child_node)
    
    fixed = parse_json_response(response)
    if not fixed:
        print(f"   ⚠️ [{node_id}] 파싱 실패, 원본 유지")
        return (idx, child_node)
    
    print(f"   ✅ [{node_id}] 완료")
    return (idx, fixed)


def process_parallel(structure_abs: Dict, image_base64: Optional[str], image_path: Path, prompts: Dict) -> Dict:
    """자식 노드들을 병렬로 처리"""
    children = structure_abs.get('children', [])
    
    if not children:
        print("   ⚠️ 자식 노드 없음, 단일 처리로 전환")
        return process_single(structure_abs, image_base64, image_path, prompts)
    
    print(f"   📊 자식 노드 {len(children)}개 병렬 처리 (workers: {MAX_WORKERS})")
    
    # 작업 준비
    tasks = [
        (idx, child, image_base64, image_path, prompts)
        for idx, child in enumerate(children)
    ]
    
    # 병렬 실행
    results = [None] * len(children)
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_child_node, task): task[0] for task in tasks}
        
        for future in as_completed(futures):
            idx, fixed_child = future.result()
            results[idx] = fixed_child
    
    # 결과 병합
    result = deepcopy(structure_abs)
    result['children'] = results
    
    return result


def process_single(structure_abs: Dict, image_base64: Optional[str], image_path: Path, prompts: Dict) -> Dict:
    """전체 구조를 단일 LLM 호출로 처리"""
    print("   🤖 단일 LLM 호출...")
    
    structure_json = json.dumps(structure_abs, ensure_ascii=False, indent=2)
    prompt = create_fix_prompt(structure_json, prompts)
    
    response = call_llm_single(prompt, image_base64, image_path, prompts, "root")
    
    if not response:
        print("   ❌ 응답 없음, 원본 유지")
        return structure_abs
    
    fixed = parse_json_response(response)
    if not fixed:
        print("   ❌ 파싱 실패, 원본 유지")
        return structure_abs
    
    print("   ✅ 완료")
    return fixed


# ============================================================
# padding/gap 계산
# ============================================================

def add_layout_properties(node: Dict) -> Dict:
    """padding, gap, direction 속성 추가 (룰베이스)"""
    node_type = node.get('type', '')
    children = node.get('children', [])
    position = node.get('position', {})
    
    if node_type in ('HStack', 'VStack', 'ZStack', 'Group'):
        if node_type == 'HStack':
            node['direction'] = 'horizontal'
        elif node_type == 'VStack':
            node['direction'] = 'vertical'
        
        if children and position:
            parent_width = position.get('width', 0)
            parent_height = position.get('height', 0)
            
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
    
    if children:
        for child in children:
            add_layout_properties(child)
    
    return node


# ============================================================
# 메인 파이프라인
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("🔧 Structure Fixer")
    print("=" * 60)
    
    if not INPUT_STRUCTURE.exists():
        print(f"❌ 입력 파일 없음: {INPUT_STRUCTURE}")
        return
    
    print(f"\n📋 설정:")
    print(f"   - 샘플: {SAMPLE_NAME}")
    print(f"   - 병렬 모드: {'ON' if PARALLEL_MODE else 'OFF'}")
    print(f"   - 동시 호출 수: {MAX_WORKERS}")
    print(f"   - 입력: {INPUT_STRUCTURE}")
    print(f"   - 이미지: {INPUT_IMAGE}")
    print(f"   - 출력: {OUTPUT_FILE}")
    
    start_time = time.time()
    
    # 1. 프롬프트 로드
    print("\n📝 Step 1: 프롬프트 로드")
    prompts = load_prompts()
    print(f"   ✅ 로드 완료")
    
    # 2. 입력 로드
    print("\n📥 Step 2: 입력 로드")
    with open(INPUT_STRUCTURE, 'r', encoding='utf-8') as f:
        structure = json.load(f)
    print(f"   ✅ 로드 완료")
    
    # 3. 절대좌표 변환
    print("\n🔄 Step 3: 절대좌표 변환")
    structure_abs = to_absolute_coords(structure)
    print(f"   ✅ 변환 완료")
    
    # 4. 이미지 인코딩
    print("\n🖼️ Step 4: 이미지 인코딩")
    image_base64 = encode_image(INPUT_IMAGE) if INPUT_IMAGE.exists() else None
    if image_base64:
        print(f"   ✅ 인코딩 완료")
    else:
        print(f"   ⚠️ 이미지 없음 (텍스트만 사용)")
    
    # 5. LLM 호출 (병렬 또는 단일)
    print("\n🤖 Step 5: LLM 구조 수정")
    
    if PARALLEL_MODE:
        fixed_abs = process_parallel(structure_abs, image_base64, INPUT_IMAGE, prompts)
    else:
        fixed_abs = process_single(structure_abs, image_base64, INPUT_IMAGE, prompts)
    
    # 6. 상대좌표 변환 (룰베이스)
    print("\n🔄 Step 6: 상대좌표 변환")
    fixed_rel = to_relative_coords(fixed_abs)
    print(f"   ✅ 변환 완료")
    
    # 7. padding/gap 계산 (룰베이스)
    print("\n📐 Step 7: padding/gap 계산")
    result = add_layout_properties(fixed_rel)
    print(f"   ✅ 계산 완료")
    
    # 8. 저장
    print("\n💾 Step 8: 결과 저장")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 저장 완료: {OUTPUT_FILE}")
    
    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"🎉 완료! (소요시간: {elapsed:.1f}초)")
    print("=" * 60)


if __name__ == "__main__":
    main()
