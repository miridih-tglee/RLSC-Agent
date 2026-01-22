#!/usr/bin/env python3
"""
Rule-based RLSC Structure Fixer

규칙:
1. Background는 컨테이너(Group/VStack/HStack/ZStack) 내 1개만 허용
2. 겹침 처리:
   - Decoration + Decoration → Group으로 묶고 큰 것 Background
   - Decoration + Marker → Group으로 묶고 큰 것 Background  
   - Marker + Marker → Group으로 묶고 큰 것 Background
   - Stack + Frame/Image → Frame/Image를 Background로 묶기
"""

import json
import uuid
from copy import deepcopy
from typing import Dict, List, Tuple, Optional, Set

# ============================================================
# 설정
# ============================================================
INPUT_PATH = "/Users/miridih/Desktop/tg/data/283782/structure_json.json"
OUTPUT_PATH = "/Users/miridih/Desktop/tg/data/283782/structure_json_r.json"


# ============================================================
# 유틸리티 함수
# ============================================================
def generate_id() -> str:
    return str(uuid.uuid4())


def get_role(node: Dict) -> str:
    """role에서 마지막 부분 추출 (Role.Element.Decoration → Decoration)"""
    role = node.get('role', '')
    if '.' in role:
        return role.split('.')[-1]
    return role


def get_type(node: Dict) -> str:
    """type 반환"""
    return node.get('type', '')


def is_background(node: Dict) -> bool:
    return get_role(node) == 'Background'


def is_decoration(node: Dict) -> bool:
    """Element.Decoration인지 확인 (LayoutContainer.Decoration은 제외)"""
    role = node.get('role', '')
    return 'Element.Decoration' in role


def is_marker(node: Dict) -> bool:
    return get_role(node) == 'Marker'


def is_frame(node: Dict) -> bool:
    return get_type(node) == 'Frame'


def is_image(node: Dict) -> bool:
    return get_type(node) == 'Image'


def is_stack(node: Dict) -> bool:
    return get_type(node) in ['VStack', 'HStack', 'ZStack']


def is_container(node: Dict) -> bool:
    return get_type(node) in ['Group', 'VStack', 'HStack', 'ZStack', 'Frame']


def get_bbox(node: Dict) -> Optional[Tuple[float, float, float, float]]:
    """(x1, y1, x2, y2) 반환"""
    pos = node.get('position', {})
    if not pos:
        return None
    x = pos.get('x', 0)
    y = pos.get('y', 0)
    w = pos.get('width', 0)
    h = pos.get('height', 0)
    return (x, y, x + w, y + h)


def get_area(node: Dict) -> float:
    pos = node.get('position', {})
    return pos.get('width', 0) * pos.get('height', 0)


def is_overlapping(bbox1: Tuple, bbox2: Tuple, threshold: float = 0.1) -> bool:
    """두 bbox가 겹치는지 확인 (IoU 기반)"""
    x1 = max(bbox1[0], bbox2[0])
    y1 = max(bbox1[1], bbox2[1])
    x2 = min(bbox1[2], bbox2[2])
    y2 = min(bbox1[3], bbox2[3])
    
    if x1 >= x2 or y1 >= y2:
        return False
    
    intersection = (x2 - x1) * (y2 - y1)
    area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
    area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
    
    # 작은 쪽 기준으로 겹침 비율 계산
    smaller_area = min(area1, area2)
    if smaller_area <= 0:
        return False
    
    overlap_ratio = intersection / smaller_area
    return overlap_ratio > threshold


# ============================================================
# 겹침 검사 함수
# ============================================================
def should_check_pair_decoration_only(node1: Dict, node2: Dict) -> bool:
    """
    Decoration/Marker끼리 겹침만 검사 (Frame 제외)
    
    검사할 쌍:
    - Decoration + Decoration
    - Decoration + Marker
    - Marker + Marker
    """
    role1, role2 = get_role(node1), get_role(node2)
    type1, type2 = get_type(node1), get_type(node2)
    
    # Background는 겹침 허용 → 검사 안 함
    if role1 == 'Background' or role2 == 'Background':
        return False
    
    # Title, Description, Text는 검사 안 함
    if role1 in ['Title', 'Description', 'Subtitle'] or type1 == 'Text':
        return False
    if role2 in ['Title', 'Description', 'Subtitle'] or type2 == 'Text':
        return False
    
    # Frame, Image는 이 단계에서 검사 안 함
    if is_frame(node1) or is_frame(node2) or is_image(node1) or is_image(node2):
        return False
    
    # Decoration + Decoration
    if role1 == 'Decoration' and role2 == 'Decoration':
        return True
    
    # Decoration + Marker
    if (role1 == 'Decoration' and role2 == 'Marker') or \
       (role1 == 'Marker' and role2 == 'Decoration'):
        return True
    
    # Marker + Marker
    if role1 == 'Marker' and role2 == 'Marker':
        return True
    
    return False


def find_overlapping_pairs(children: List[Dict]) -> List[Tuple[int, int]]:
    """겹치는 Decoration/Marker 쌍 찾기 (Frame 제외)"""
    pairs = []
    
    for i in range(len(children)):
        bbox_i = get_bbox(children[i])
        if not bbox_i:
            continue
        
        for j in range(i + 1, len(children)):
            bbox_j = get_bbox(children[j])
            if not bbox_j:
                continue
            
            # Decoration/Marker끼리만 검사 (Frame 제외)
            if not should_check_pair_decoration_only(children[i], children[j]):
                continue
            
            # 겹침 확인
            if is_overlapping(bbox_i, bbox_j):
                pairs.append((i, j))
    
    return pairs


# ============================================================
# Union-Find로 그룹화
# ============================================================
def group_overlapping(children: List[Dict], pairs: List[Tuple[int, int]]) -> List[List[int]]:
    """Union-Find로 겹치는 요소들 그룹화"""
    if not pairs:
        return []
    
    parent = list(range(len(children)))
    
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    for i, j in pairs:
        union(i, j)
    
    # 그룹 수집
    groups = {}
    for i, j in pairs:
        for idx in [i, j]:
            root = find(idx)
            if root not in groups:
                groups[root] = set()
            groups[root].add(idx)
    
    # 2개 이상인 그룹만 반환
    return [list(g) for g in groups.values() if len(g) >= 2]


# ============================================================
# 그룹 묶기
# ============================================================
def determine_background_in_group(nodes: List[Dict]) -> int:
    """그룹 내에서 Background가 될 요소 결정
    
    항상 가장 큰 요소를 Background로!
    (Decoration끼리 겹칠 때 큰 것이 Background가 되어야 함)
    """
    max_area = -1
    max_idx = 0
    for i, node in enumerate(nodes):
        area = get_area(node)
        if area > max_area:
            max_area = area
            max_idx = i
    
    return max_idx


def wrap_in_group(nodes: List[Dict]) -> Dict:
    """노드들을 Group으로 감싸기 (절대좌표 상태에서 호출됨)"""
    if not nodes:
        return {}
    
    # Background가 될 요소 결정
    bg_idx = determine_background_in_group(nodes)
    
    # 감싸는 Group의 bounds 계산 (절대좌표 기준)
    all_bboxes = [get_bbox(n) for n in nodes if get_bbox(n)]
    if all_bboxes:
        min_x = min(b[0] for b in all_bboxes)
        min_y = min(b[1] for b in all_bboxes)
        max_x = max(b[2] for b in all_bboxes)
        max_y = max(b[3] for b in all_bboxes)
    else:
        min_x = min_y = 0
        max_x = max_y = 100
    
    # 자식 생성 (Background 설정만, 좌표는 나중에 to_relative_coords에서 변환)
    wrapped_children = []
    for i, node in enumerate(nodes):
        node_copy = deepcopy(node)
        
        # Background 설정
        if i == bg_idx:
            node_copy['role'] = 'Role.Element.Background'
            print(f"      → Background: {node_copy.get('id', 'unknown')[:20]}")
        
        # 좌표는 절대좌표 그대로 유지 (나중에 to_relative_coords에서 변환)
        wrapped_children.append(node_copy)
    
    return {
        'id': generate_id(),
        'role': 'Role.LayoutContainer.Decoration',
        'type': 'Group',
        'children': wrapped_children,
        'position': {
            'x': round(min_x, 2),
            'y': round(min_y, 2),
            'width': round(max_x - min_x, 2),
            'height': round(max_y - min_y, 2)
        }
    }


# ============================================================
# Background 중복 처리
# ============================================================
def fix_multiple_backgrounds(children: List[Dict]) -> List[Dict]:
    """컨테이너에 Background가 여러 개면 하나만 남기고 나머지는 Decoration으로"""
    backgrounds = [i for i, c in enumerate(children) if is_background(c)]
    
    if len(backgrounds) <= 1:
        return children
    
    # 가장 큰 Background만 유지
    bg_areas = [(i, get_area(children[i])) for i in backgrounds]
    bg_areas.sort(key=lambda x: x[1], reverse=True)
    largest_bg_idx = bg_areas[0][0]
    
    result = []
    for i, child in enumerate(children):
        child_copy = deepcopy(child)
        if i in backgrounds and i != largest_bg_idx:
            # 가장 큰 것 외에는 Decoration으로
            child_copy['role'] = 'Role.Element.Decoration'
            print(f"      → Background 중복 해소: {child_copy.get('id', 'unknown')[:20]} → Decoration")
        result.append(child_copy)
    
    return result


# ============================================================
# 메인 수정 함수
# ============================================================
def find_background_candidate(children: List[Dict]) -> int:
    """
    배경 후보 찾기: 컨테이너 크기의 대부분을 차지하는 가장 큰 Decoration
    이미 Background인 것은 제외
    """
    # Decoration들 중 가장 큰 것 찾기
    max_area = -1
    max_idx = -1
    
    for i, child in enumerate(children):
        if is_decoration(child) and not is_background(child):
            area = get_area(child)
            if area > max_area:
                max_area = area
                max_idx = i
    
    return max_idx


def fix_node(node: Dict, depth: int = 0) -> Dict:
    """노드와 그 자식들의 겹침 문제 수정"""
    indent = "  " * depth
    result = deepcopy(node)
    children = result.get('children', [])
    
    if not children:
        return result
    
    node_id = node.get('id', 'unknown')[:20]
    print(f"{indent}📁 처리 중: {node_id} (type: {get_type(node)})")
    
    # 1. 자식들 먼저 재귀 처리
    children = [fix_node(c, depth + 1) for c in children]
    
    # 2. Background 중복 수정
    children = fix_multiple_backgrounds(children)
    
    # 3. 먼저 겹침 검사
    overlapping_pairs = find_overlapping_pairs(children)
    
    # 4. 겹침이 있을 때만! 가장 큰 Decoration → Background
    if overlapping_pairs:
        print(f"{indent}   ⚠️ 겹침 발견: {len(overlapping_pairs)}쌍")
        
        bg_candidate_idx = find_background_candidate(children)
        if bg_candidate_idx >= 0:
            bg_candidate = children[bg_candidate_idx]
            children[bg_candidate_idx] = deepcopy(bg_candidate)
            children[bg_candidate_idx]['role'] = 'Role.Element.Background'
            print(f"{indent}   🎨 겹침 발견 → 가장 큰 Deco → BG: {bg_candidate.get('id', 'unknown')[:20]}")
        
        # 5. 다시 겹침 검사 (Background 제외됨)
        overlapping_pairs = find_overlapping_pairs(children)
        
        # 6. 아직 겹치면 Group으로 묶기
        if overlapping_pairs:
            groups = group_overlapping(children, overlapping_pairs)
            
            if groups:
                grouped_indices = set()
                for g in groups:
                    grouped_indices.update(g)
                
                new_children = []
                
                for i, child in enumerate(children):
                    if i not in grouped_indices:
                        new_children.append(child)
                
                for group_indices in groups:
                    group_nodes = [children[i] for i in group_indices]
                    ids = [n.get('id', '?')[:15] for n in group_nodes]
                    print(f"{indent}   📦 그룹 생성: {ids}")
                    
                    wrapped = wrap_in_group(group_nodes)
                    # ✅ 새로 생성된 Group도 재귀적으로 fix!
                    wrapped = fix_node(wrapped, depth + 1, verbose)
                    new_children.append(wrapped)
                
                children = new_children
    
    # 7. Background 중복 확인 (그룹 묶은 후)
    children = fix_multiple_backgrounds(children)
    
    result['children'] = children
    return result


# ============================================================
# 좌표 변환 함수
# ============================================================
def to_absolute_coords(node: Dict, parent_x: float = 0, parent_y: float = 0) -> Dict:
    """상대좌표 → 절대좌표"""
    result = deepcopy(node)
    
    pos = result.get('position', {})
    if pos:
        abs_x = parent_x + pos.get('x', 0)
        abs_y = parent_y + pos.get('y', 0)
        pos['x'] = abs_x
        pos['y'] = abs_y
    else:
        abs_x, abs_y = parent_x, parent_y
    
    children = result.get('children', [])
    if children:
        result['children'] = [
            to_absolute_coords(c, abs_x, abs_y) for c in children
        ]
    
    return result


def to_relative_coords(node: Dict, parent_x: float = 0, parent_y: float = 0) -> Dict:
    """절대좌표 → 상대좌표"""
    result = deepcopy(node)
    
    pos = result.get('position', {})
    if pos:
        abs_x = pos.get('x', 0)
        abs_y = pos.get('y', 0)
        pos['x'] = round(abs_x - parent_x, 2)
        pos['y'] = round(abs_y - parent_y, 2)
    else:
        abs_x, abs_y = parent_x, parent_y
    
    children = result.get('children', [])
    if children:
        result['children'] = [
            to_relative_coords(c, abs_x, abs_y) for c in children
        ]
    
    return result


# ============================================================
# Layout Properties (padding, gap, direction)
# ============================================================
def add_layout_properties(node: Dict) -> Dict:
    """padding, gap, direction 추가"""
    result = deepcopy(node)
    node_type = get_type(result)
    
    # direction 설정
    if node_type == 'HStack':
        result['direction'] = 'horizontal'
    elif node_type == 'VStack':
        result['direction'] = 'vertical'
    
    children = result.get('children', [])
    if not children:
        return result
    
    # 자식들 먼저 재귀 처리
    result['children'] = [add_layout_properties(c) for c in children]
    children = result['children']
    
    # padding 계산 (부모 bounds와 자식들 사이 간격)
    parent_pos = result.get('position', {})
    parent_w = parent_pos.get('width', 0)
    parent_h = parent_pos.get('height', 0)
    
    if parent_w > 0 and parent_h > 0 and children:
        child_bboxes = [get_bbox(c) for c in children if get_bbox(c)]
        if child_bboxes:
            min_x = min(b[0] for b in child_bboxes)
            min_y = min(b[1] for b in child_bboxes)
            max_x = max(b[2] for b in child_bboxes)
            max_y = max(b[3] for b in child_bboxes)
            
            result['padding'] = {
                'top': round(max(0, min_y), 2),
                'bottom': round(max(0, parent_h - max_y), 2),
                'left': round(max(0, min_x), 2),
                'right': round(max(0, parent_w - max_x), 2)
            }
    
    # gap 계산 (자식들 사이 간격)
    if len(children) >= 2 and node_type in ['HStack', 'VStack']:
        gaps = []
        sorted_children = sorted(children, key=lambda c: c.get('position', {}).get('x' if node_type == 'HStack' else 'y', 0))
        
        for i in range(len(sorted_children) - 1):
            bbox1 = get_bbox(sorted_children[i])
            bbox2 = get_bbox(sorted_children[i + 1])
            if bbox1 and bbox2:
                if node_type == 'HStack':
                    gap = bbox2[0] - bbox1[2]  # x2 - x1
                else:
                    gap = bbox2[1] - bbox1[3]  # y2 - y1
                if gap > 0:
                    gaps.append(gap)
        
        if gaps:
            result['gap'] = round(sum(gaps) / len(gaps), 2)
    
    return result


# ============================================================
# 메인
# ============================================================
def main():
    print("=" * 60)
    print("🔧 Rule-based RLSC Fixer")
    print("=" * 60)
    
    input_path = INPUT_PATH
    output_path = OUTPUT_PATH
    
    # 1. 로드
    print("\n📥 Step 1: 입력 로드")
    with open(input_path, 'r', encoding='utf-8') as f:
        structure = json.load(f)
    print(f"   ✅ {input_path}")
    
    # 2. 절대좌표 변환
    print("\n🔄 Step 2: 절대좌표 변환")
    structure_abs = to_absolute_coords(structure)
    print("   ✅ 완료")
    
    # 3. 겹침 수정
    print("\n🔧 Step 3: 겹침 수정")
    fixed_abs = fix_node(structure_abs)
    print("   ✅ 완료")
    
    # 4. 상대좌표 변환
    print("\n🔄 Step 4: 상대좌표 변환")
    fixed_rel = to_relative_coords(fixed_abs)
    print("   ✅ 완료")
    
    # 5. Layout properties
    print("\n📐 Step 5: padding/gap/direction 추가")
    result = add_layout_properties(fixed_rel)
    print("   ✅ 완료")
    
    # 6. 저장
    print("\n💾 Step 6: 저장")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"   ✅ {output_path}")
    
    print("\n" + "=" * 60)
    print("🎉 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
