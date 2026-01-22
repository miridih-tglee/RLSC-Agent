#!/usr/bin/env python3
"""
Structure Validator & Fixer: RLSC 구조의 규칙 위반을 감지하고 수정하는 시스템

규칙:
1. Decoration 요소들은 서로 겹치면 안 됨 (Background 제외)
2. Background는 겹침 허용되지만, 하나의 컨테이너에 하나만 존재
3. VStack/HStack에서는 겹침이 있으면 안 됨 (ZStack/Group만 허용)

접근법: Surgical Fix (부분 수정)
1. 규칙 위반 감지 (Rule-based) → 문제 노드의 path 반환
2. 컨텍스트 추출 → 해당 노드 + 부모 + 자식 정보
3. LLM 부분 수정 → 위반된 subtree만 수정 요청
4. 병합 → 원본에 수정된 부분만 교체
"""

import json
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from copy import deepcopy


# ============================================================
# 🔧 설정 변수
# ============================================================

INPUT_STRUCTURE = "data/277987/structure_json.json"
INPUT_IMAGE = "data/277987/thumbnail.png"
OUTPUT_FILE = "data/277987/structure_fixed.json"

# 병렬 처리 설정
USE_PARALLEL = True  # 병렬 처리 사용 여부
MAX_CONCURRENT = 5   # 최대 동시 LLM 요청 수

# ============================================================
# 데이터 클래스
# ============================================================

@dataclass
class Violation:
    """규칙 위반 정보"""
    violation_type: str  # overlap_decoration, multiple_backgrounds, overlap_in_stack
    path: List[int]  # 노드까지의 인덱스 경로 [0, 2, 1]
    node_id: str
    node_type: str
    description: str
    involved_elements: List[str] = field(default_factory=list)
    severity: str = "warning"  # warning, error


@dataclass
class BoundingBox:
    """요소의 경계 상자"""
    x: float
    y: float
    width: float
    height: float
    
    def overlaps(self, other: 'BoundingBox', threshold: float = 0.1) -> bool:
        """두 박스가 겹치는지 확인 (threshold: 최소 겹침 비율)"""
        # 겹치지 않는 경우
        if (self.x + self.width <= other.x or 
            other.x + other.width <= self.x or
            self.y + self.height <= other.y or 
            other.y + other.height <= self.y):
            return False
        
        # 겹치는 영역 계산
        overlap_x = max(0, min(self.x + self.width, other.x + other.width) - max(self.x, other.x))
        overlap_y = max(0, min(self.y + self.height, other.y + other.height) - max(self.y, other.y))
        overlap_area = overlap_x * overlap_y
        
        # 더 작은 요소 기준으로 겹침 비율 계산
        min_area = min(self.width * self.height, other.width * other.height)
        if min_area == 0:
            return False
        
        return (overlap_area / min_area) > threshold


# ============================================================
# 유틸리티 함수
# ============================================================

def get_node_bbox(node: Dict) -> Optional[BoundingBox]:
    """노드의 BoundingBox 반환"""
    pos = node.get('position', {})
    if not pos:
        return None
    return BoundingBox(
        x=pos.get('x', 0),
        y=pos.get('y', 0),
        width=pos.get('width', 0),
        height=pos.get('height', 0)
    )


def convert_to_absolute_coords(node: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> Dict:
    """
    상대 좌표를 절대 좌표로 변환 (재귀적)
    원본을 수정하지 않고 새 딕셔너리 반환
    """
    result = {}
    
    # 기본 속성 복사
    for key, value in node.items():
        if key not in ('position', 'children'):
            result[key] = value
    
    # position을 절대 좌표로 변환
    pos = node.get('position', {})
    if pos:
        abs_x = parent_abs_x + pos.get('x', 0)
        abs_y = parent_abs_y + pos.get('y', 0)
        result['position'] = {
            'x': abs_x,
            'y': abs_y,
            'width': pos.get('width', 0),
            'height': pos.get('height', 0)
        }
        result['_abs_x'] = abs_x  # 자식 계산용 임시 저장
        result['_abs_y'] = abs_y
    else:
        result['_abs_x'] = parent_abs_x
        result['_abs_y'] = parent_abs_y
    
    # 자식들도 재귀적으로 변환
    children = node.get('children', [])
    if children:
        result['children'] = [
            convert_to_absolute_coords(child, result['_abs_x'], result['_abs_y'])
            for child in children
        ]
    
    return result


def convert_to_relative_coords(node: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> Dict:
    """
    절대 좌표를 상대 좌표로 변환 (재귀적)
    """
    result = {}
    
    # 기본 속성 복사 (임시 속성 제외)
    for key, value in node.items():
        if key not in ('position', 'children', '_abs_x', '_abs_y'):
            result[key] = value
    
    # position을 상대 좌표로 변환
    pos = node.get('position', {})
    abs_x = pos.get('x', 0)
    abs_y = pos.get('y', 0)
    
    if pos:
        result['position'] = {
            'x': abs_x - parent_abs_x,
            'y': abs_y - parent_abs_y,
            'width': pos.get('width', 0),
            'height': pos.get('height', 0)
        }
    
    # 자식들도 재귀적으로 변환
    children = node.get('children', [])
    if children:
        result['children'] = [
            convert_to_relative_coords(child, abs_x, abs_y)
            for child in children
        ]
    
    return result


def recalculate_parent_bounds(node: Dict) -> Dict:
    """
    자식들의 bounds를 기반으로 부모의 position 재계산 (재귀적, bottom-up)
    절대 좌표 기준으로 작동
    """
    children = node.get('children', [])
    
    # 먼저 자식들의 bounds 재계산 (bottom-up)
    if children:
        for child in children:
            recalculate_parent_bounds(child)
        
        # 자식들의 bounding box 계산
        child_positions = [c.get('position', {}) for c in children if c.get('position')]
        
        if child_positions:
            min_x = min(p.get('x', 0) for p in child_positions)
            min_y = min(p.get('y', 0) for p in child_positions)
            max_x = max(p.get('x', 0) + p.get('width', 0) for p in child_positions)
            max_y = max(p.get('y', 0) + p.get('height', 0) for p in child_positions)
            
            node['position'] = {
                'x': min_x,
                'y': min_y,
                'width': max_x - min_x,
                'height': max_y - min_y
            }
    
    return node


def is_background_role(role: str) -> bool:
    """Background role인지 확인"""
    return 'Background' in role


def is_decoration_role(role: str) -> bool:
    """Decoration role인지 확인"""
    return 'Decoration' in role


def get_node_by_path(root: Dict, path: List[int]) -> Optional[Dict]:
    """path로 노드 찾기"""
    node = root
    for idx in path:
        children = node.get('children', [])
        if idx >= len(children):
            return None
        node = children[idx]
    return node


def set_node_by_path(root: Dict, path: List[int], new_node: Dict) -> bool:
    """path의 노드를 교체"""
    if not path:
        # root 자체를 교체하는 경우
        root.clear()
        root.update(new_node)
        return True
    
    parent_path = path[:-1]
    child_idx = path[-1]
    
    parent = root
    for idx in parent_path:
        children = parent.get('children', [])
        if idx >= len(children):
            return False
        parent = children[idx]
    
    children = parent.get('children', [])
    if child_idx >= len(children):
        return False
    
    children[child_idx] = new_node
    return True


def encode_image_to_base64(image_path: str) -> Optional[str]:
    """이미지를 base64로 인코딩"""
    try:
        path = Path(image_path)
        if not path.exists():
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except:
        return None


# ============================================================
# Phase 1: 규칙 위반 감지
# ============================================================

class ViolationDetector:
    """규칙 위반 감지기"""
    
    def __init__(self):
        self.violations: List[Violation] = []
    
    def detect_all(self, root: Dict) -> List[Violation]:
        """모든 규칙 위반 감지"""
        self.violations = []
        self._traverse(root, [])
        return self.violations
    
    def _traverse(self, node: Dict, path: List[int]):
        """노드 순회하며 위반 감지"""
        children = node.get('children', [])
        node_type = node.get('type', '')
        
        if children:
            # 1. 자식들 간의 겹침 검사
            self._check_overlaps(node, path)
            
            # 2. Background 중복 검사
            self._check_multiple_backgrounds(node, path)
            
            # 3. VStack/HStack에서 겹침 검사
            self._check_stack_overlaps(node, path)
            
            # 자식들 재귀 순회
            for i, child in enumerate(children):
                self._traverse(child, path + [i])
    
    def _check_overlaps(self, node: Dict, path: List[int]):
        """Decoration 요소들 간의 겹침 검사"""
        node_type = node.get('type', '')
        children = node.get('children', [])
        
        # ZStack/Group은 겹침 허용 → 검사 스킵
        if node_type in ('ZStack', 'Group'):
            return
        
        # Decoration이면서 Background가 아닌 요소들만 추출
        decorations = []
        for i, child in enumerate(children):
            role = child.get('role', '')
            if is_decoration_role(role) and not is_background_role(role):
                bbox = get_node_bbox(child)
                if bbox:
                    decorations.append({
                        'index': i,
                        'id': child.get('id', ''),
                        'role': role,
                        'bbox': bbox
                    })
        
        # 겹침 검사
        overlapping_pairs = []
        for i in range(len(decorations)):
            for j in range(i + 1, len(decorations)):
                if decorations[i]['bbox'].overlaps(decorations[j]['bbox']):
                    overlapping_pairs.append((decorations[i], decorations[j]))
        
        if overlapping_pairs:
            involved = list(set(
                [p[0]['id'] for p in overlapping_pairs] + 
                [p[1]['id'] for p in overlapping_pairs]
            ))
            
            self.violations.append(Violation(
                violation_type="overlap_decoration",
                path=path,
                node_id=node.get('id', ''),
                node_type=node.get('type', ''),
                description=f"{len(overlapping_pairs)}개의 Decoration 요소 쌍이 겹침 (ZStack/Group이 아닌 {node_type}에서)",
                involved_elements=involved,
                severity="error"
            ))
    
    def _check_multiple_backgrounds(self, node: Dict, path: List[int]):
        """Background 중복 검사"""
        children = node.get('children', [])
        
        backgrounds = [
            child.get('id', '')
            for child in children
            if is_background_role(child.get('role', ''))
        ]
        
        if len(backgrounds) > 1:
            self.violations.append(Violation(
                violation_type="multiple_backgrounds",
                path=path,
                node_id=node.get('id', ''),
                node_type=node.get('type', ''),
                description=f"Background가 {len(backgrounds)}개 존재 (1개만 허용)",
                involved_elements=backgrounds,
                severity="error"
            ))
    
    def _check_stack_overlaps(self, node: Dict, path: List[int]):
        """VStack/HStack에서 겹침 검사 (ZStack/Group이 아닌 경우)"""
        node_type = node.get('type', '')
        
        # ZStack, Group은 겹침 허용
        if node_type in ('ZStack', 'Group'):
            return
        
        # VStack, HStack에서 겹침 검사
        if node_type not in ('VStack', 'HStack'):
            return
        
        children = node.get('children', [])
        if len(children) < 2:
            return
        
        # 모든 자식 쌍에 대해 겹침 검사
        overlapping_children = []
        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                bbox_i = get_node_bbox(children[i])
                bbox_j = get_node_bbox(children[j])
                
                if bbox_i and bbox_j and bbox_i.overlaps(bbox_j, threshold=0.05):
                    overlapping_children.append((
                        children[i].get('id', ''),
                        children[j].get('id', '')
                    ))
        
        if overlapping_children:
            involved = list(set(
                [p[0] for p in overlapping_children] + 
                [p[1] for p in overlapping_children]
            ))
            
            self.violations.append(Violation(
                violation_type="overlap_in_stack",
                path=path,
                node_id=node.get('id', ''),
                node_type=node_type,
                description=f"{node_type}에서 {len(overlapping_children)}쌍의 자식이 겹침 (ZStack/Group으로 변경 필요?)",
                involved_elements=involved,
                severity="warning"
            ))


# ============================================================
# Phase 2: 컨텍스트 추출
# ============================================================

def extract_context(root: Dict, violation: Violation) -> Dict:
    """위반된 노드의 컨텍스트 추출 (절대 좌표 포함)"""
    node = get_node_by_path(root, violation.path)
    if not node:
        return {}
    
    # 부모 노드
    parent = None
    parent_abs_x = 0
    parent_abs_y = 0
    
    if violation.path:
        parent_path = violation.path[:-1]
        parent = get_node_by_path(root, parent_path) if parent_path else root
        
        # 부모까지의 절대 좌표 계산
        current = root
        for idx in parent_path:
            pos = current.get('position', {})
            parent_abs_x += pos.get('x', 0)
            parent_abs_y += pos.get('y', 0)
            children = current.get('children', [])
            if idx < len(children):
                current = children[idx]
    
    # 형제 노드들 (같은 레벨)
    siblings = []
    if parent and 'children' in parent:
        siblings = [
            {'id': s.get('id', ''), 'type': s.get('type', ''), 'role': s.get('role', '')}
            for s in parent.get('children', [])
            if s.get('id') != node.get('id')
        ]
    
    # 노드를 절대 좌표로 변환 (LLM이 이미지와 매칭할 수 있도록)
    node_with_abs_coords = convert_to_absolute_coords(node, parent_abs_x, parent_abs_y)
    
    return {
        'violation': {
            'type': violation.violation_type,
            'description': violation.description,
            'involved_elements': violation.involved_elements,
            'severity': violation.severity
        },
        'node': node,  # 원본 (상대 좌표)
        'node_absolute': node_with_abs_coords,  # 절대 좌표 변환본
        'node_path': violation.path,
        'parent_abs_position': {'x': parent_abs_x, 'y': parent_abs_y},
        'parent': {
            'id': parent.get('id', '') if parent else None,
            'type': parent.get('type', '') if parent else None,
            'role': parent.get('role', '') if parent else None
        },
        'siblings_count': len(siblings)
    }


# ============================================================
# Phase 3: LLM 부분 수정
# ============================================================

class StructureFixer:
    """LLM을 사용한 구조 수정기"""
    
    def __init__(self, llm_client=None, image_path: Optional[str] = None):
        self.llm_client = llm_client
        self.image_base64 = encode_image_to_base64(image_path) if image_path else None
    
    def fix_violation(self, context: Dict) -> Optional[Dict]:
        """위반된 노드를 수정 (절대/상대 좌표 변환 포함)"""
        if not self.llm_client:
            return self._apply_rule_based_fix(context)
        
        # LLM 수정 결과 (절대 좌표로 반환됨)
        fixed_node_abs = self._apply_llm_fix(context)
        
        if fixed_node_abs:
            # 부모의 절대 좌표 기준으로 상대 좌표로 변환
            parent_abs = context.get('parent_abs_position', {'x': 0, 'y': 0})
            fixed_node_rel = convert_to_relative_coords(fixed_node_abs, parent_abs['x'], parent_abs['y'])
            return fixed_node_rel
        
        return None
    
    def _apply_rule_based_fix(self, context: Dict) -> Optional[Dict]:
        """규칙 기반 자동 수정 (LLM 없이) - 최소한의 변경"""
        node = deepcopy(context['node'])
        violation_type = context['violation']['type']
        
        if violation_type == "overlap_decoration":
            # 겹치는 Decoration이 있으면 → 부모 타입을 ZStack으로 변경 (가장 단순한 해결책)
            # 자식 구조는 그대로 유지
            node['type'] = 'ZStack'
            return node
        
        elif violation_type == "multiple_backgrounds":
            # Background가 여러 개면 → 가장 큰 것만 유지, 나머지는 Decoration으로
            return self._keep_single_background(node, context['violation']['involved_elements'])
        
        elif violation_type == "overlap_in_stack":
            # VStack/HStack에서 겹침 → type을 ZStack으로 변경
            node['type'] = 'ZStack'
            return node
        
        return node
    
    def _wrap_overlapping_decorations(self, node: Dict, involved_ids: List[str]) -> Dict:
        """겹치는 Decoration들을 ZStack으로 감싸기"""
        children = node.get('children', [])
        
        # 겹치는 요소들과 그렇지 않은 요소들 분리
        overlapping = []
        non_overlapping = []
        
        for child in children:
            if child.get('id') in involved_ids:
                overlapping.append(child)
            else:
                non_overlapping.append(child)
        
        if len(overlapping) <= 1:
            return node
        
        # 겹치는 요소들의 bounding box 계산
        min_x = min(c.get('position', {}).get('x', 0) for c in overlapping)
        min_y = min(c.get('position', {}).get('y', 0) for c in overlapping)
        max_x = max(c.get('position', {}).get('x', 0) + c.get('position', {}).get('width', 0) for c in overlapping)
        max_y = max(c.get('position', {}).get('y', 0) + c.get('position', {}).get('height', 0) for c in overlapping)
        
        # 겹치는 요소들의 상대 좌표 조정
        for child in overlapping:
            pos = child.get('position', {})
            pos['x'] = pos.get('x', 0) - min_x
            pos['y'] = pos.get('y', 0) - min_y
        
        # ZStack 생성
        zstack = {
            'id': f"zstack_grouped_{node.get('id', '')[:8]}",
            'type': 'ZStack',
            'role': 'Role.LayoutContainer.Decoration',
            'children': overlapping,
            'position': {
                'x': min_x,
                'y': min_y,
                'width': max_x - min_x,
                'height': max_y - min_y
            }
        }
        
        # 새로운 children 구성 (ZStack을 첫 번째로)
        new_children = [zstack] + non_overlapping
        
        # y좌표 기준 정렬 (VStack인 경우)
        if node.get('type') == 'VStack':
            new_children.sort(key=lambda c: c.get('position', {}).get('y', 0))
        elif node.get('type') == 'HStack':
            new_children.sort(key=lambda c: c.get('position', {}).get('x', 0))
        
        node['children'] = new_children
        return node
    
    def _keep_single_background(self, node: Dict, background_ids: List[str]) -> Dict:
        """첫 번째 Background만 유지, 나머지는 Decoration으로 변경"""
        children = node.get('children', [])
        
        first_bg = True
        for child in children:
            if child.get('id') in background_ids:
                if first_bg:
                    first_bg = False
                else:
                    # 두 번째 이후 Background는 Decoration으로 변경
                    role = child.get('role', '')
                    child['role'] = role.replace('Background', 'Decoration')
        
        return node
    
    def _apply_llm_fix(self, context: Dict) -> Optional[Dict]:
        """LLM을 사용한 구조 수정"""
        prompt = self._create_fix_prompt(context)
        
        try:
            if self.image_base64:
                user_content = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{self.image_base64}",
                            "detail": "high"
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            else:
                user_content = prompt
            
            response = self.llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": """당신은 Structured Content의 Role 시스템 전문가입니다.
이미지와 요소 구조를 보고, 규칙 위반을 수정하여 올바른 JSON 구조를 반환합니다.

핵심 원칙:
1. **이미지를 먼저 분석** - 좌표와 이미지를 대조하여 각 요소의 실제 배치 파악
2. **적절한 컨테이너 타입 선택**:
   - HStack: 가로 배열
   - VStack: 세로 배열
   - ZStack: 의도적 겹침 (레이어링)
   - Group: 불규칙 배치
3. **Background는 그룹당 1개** - 다른 요소들과 겹쳐있고 가장 큰 요소
4. **기존 구조 최대한 유지** - 타입 변경이나 role 변경으로 해결 가능하면 그렇게

출력 규칙:
- JSON만 출력 (```json 블록 사용)
- 절대 좌표 유지 (시스템이 자동으로 상대 좌표로 변환함)
- 기존 요소의 id, position 유지"""
                    },
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1,
                max_tokens=4000
            )
            
            return self._parse_response(response.choices[0].message.content)
        
        except Exception as e:
            print(f"⚠️ LLM 수정 실패: {e}")
            return self._apply_rule_based_fix(context)
    
    def _get_design_rules(self) -> str:
        """structure_design.yaml의 설계 규칙 반환"""
        return """### ⭐ 핵심 설계 규칙 (structure_design.yaml 기반)

#### 1. Background 규칙
- **각 Group/HStack/VStack에 Background는 1개만**
- 가장 크고 뒤에 있는 요소 = 해당 그룹의 `Role.Element.Background`
- 겹치는 요소들이 있으면 → 그 요소들을 Group/ZStack으로 묶고, 그 안에서 가장 큰 것이 Background
- Background는 다른 요소들과 겹쳐있을 때만 Background임. 겹치지 않으면 Background가 아님

#### 2. Decoration 겹침 규칙
- **Decoration끼리 겹치면 안 됨** → 겹치면 Group/ZStack으로 묶어야 함
- 원형 배경 + 아이콘 = 하나의 `Role.LayoutContainer.Marker` 그룹
  - 안에서: 원형 = `Role.Element.Background`, 아이콘 = `Role.Element.Marker`

#### 3. Separator 규칙
- 카드/그룹 **사이**에 있는 `+`, `-`, `>`, `|` 같은 기호
- **다른 그룹에 포함시키면 안 됨!**
- 별도의 `Role.Element.Separator` 또는 `Role.Element.Decoration`으로 분리

#### 4. 컨테이너 타입 규칙
- 가로 배열 → `HStack` (direction: horizontal)
- 세로 배열 → `VStack` (direction: vertical)
- 겹침/불규칙 → `ZStack` 또는 `Group`

#### 5. Role 제약
- Element.Background: 부모당 1개만, 부모 영역 대부분 차지
- Element.Decoration: 겹침 불가, 겹치면 Group으로 묶어야 함
- Element.Separator: 별도 요소로 분리
- Element.Marker: LayoutContainer.Marker 내에서만 사용"""
    
    def _create_fix_prompt(self, context: Dict) -> str:
        """수정 요청 프롬프트 생성 (절대 좌표 + structure_design.yaml 규칙)"""
        # 절대 좌표 버전 사용 (이미지와 매칭 가능)
        node_abs = context.get('node_absolute', context['node'])
        node_json = json.dumps(node_abs, ensure_ascii=False, indent=2)
        design_rules = self._get_design_rules()
        
        return f"""## 규칙 위반 수정 요청

{design_rules}

---

### ⚠️ 중요: 좌표 정보
- 아래 노드 구조의 position은 **절대 좌표** (이미지 전체 기준)입니다.
- 이미지와 좌표를 비교하여 각 요소의 시각적 위치를 파악하세요.
- 수정 후에도 **절대 좌표**를 유지해서 반환하세요.

### 위반 정보
- 유형: {context['violation']['type']}
- 설명: {context['violation']['description']}
- 관련 요소: {context['violation']['involved_elements']}
- 심각도: {context['violation']['severity']}

### 현재 노드 구조 (절대 좌표)
```json
{node_json}
```

### 부모 정보
- ID: {context['parent']['id']}
- Type: {context['parent']['type']}
- Role: {context['parent']['role']}
- 부모 절대 위치: ({context.get('parent_abs_position', {}).get('x', 0)}, {context.get('parent_abs_position', {}).get('y', 0)})

---

### 수정 요청
위 규칙을 참고하여 위반을 수정한 JSON 구조를 반환하세요.

**수정 방법 - 이미지를 보고 적절한 컨테이너 타입을 선택하세요:**

### 컨테이너 타입 선택 기준:
- **HStack**: 요소들이 **가로로 나열**되어 있을 때
- **VStack**: 요소들이 **세로로 나열**되어 있을 때  
- **ZStack**: 요소들이 **의도적으로 겹쳐**있을 때 (레이어링)
- **Group**: 불규칙한 배치이거나 특수한 경우

### 위반별 해결:
1. `overlap_decoration`: 이미지에서 해당 요소들의 배치를 확인하고:
   - 겹침이 의도적 → 부모를 ZStack으로
   - 큰 요소가 배경 역할 → 큰 것을 Background role로 변경
   - 원형+아이콘 패턴 → LayoutContainer.Marker로 묶기

2. `multiple_backgrounds`: 가장 크고 뒤에 있는 것만 Background 유지

3. `overlap_in_stack`: 이미지를 보고 실제 배치에 맞는 타입으로 변경

**⚠️ 중요:**
- 이미지에서 요소들의 실제 배치를 확인하세요
- 기존 요소들의 id, position은 유지
- 필요한 경우에만 새 그룹 노드 생성

JSON만 출력하세요 (```json 블록 사용)."""
    
    def _parse_response(self, response: str) -> Optional[Dict]:
        """LLM 응답 파싱"""
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
        except:
            return None


# ============================================================
# Phase 4: 전체 파이프라인
# ============================================================

class StructureValidator:
    """전체 검증 및 수정 파이프라인 (병렬 처리 지원)"""
    
    def __init__(self, llm_client=None, image_path: Optional[str] = None,
                 use_parallel: bool = True, max_concurrent: int = 5):
        self.detector = ViolationDetector()
        self.fixer = StructureFixer(llm_client, image_path)
        self.image_path = image_path
        self.use_parallel = use_parallel and llm_client is not None
        self.max_concurrent = max_concurrent
    
    def validate_and_fix(self, structure: Dict) -> Tuple[Dict, List[Violation], List[Dict]]:
        """
        구조 검증 및 수정
        
        Returns:
            (수정된 구조, 발견된 위반 목록, 수정 로그)
        """
        print("\n" + "=" * 60)
        print("🔍 Structure Validator & Fixer")
        print("=" * 60)
        
        result = deepcopy(structure)
        fix_log = []
        
        # Phase 1: 위반 감지
        print("\n📋 Phase 1: 규칙 위반 감지")
        violations = self.detector.detect_all(result)
        
        if not violations:
            print("   ✅ 규칙 위반 없음!")
            return result, violations, fix_log
        
        print(f"   ⚠️ {len(violations)}개 위반 발견:")
        for v in violations:
            print(f"      - [{v.severity}] {v.violation_type}: {v.description}")
            print(f"        경로: {v.path}, 노드: {v.node_id}")
        
        # Phase 2 & 3: 위반 수정
        if self.use_parallel:
            print(f"\n🔧 Phase 2-3: 위반 수정 (⚡병렬 처리, 동시 {self.max_concurrent}개)")
            fix_log = asyncio.run(self._fix_violations_parallel(result, violations))
        else:
            print("\n🔧 Phase 2-3: 위반 수정 (순차 처리)")
            fix_log = self._fix_violations_sequential(result, violations)
        
        # 수정 후 재검증
        print("\n🔄 수정 후 재검증...")
        remaining = self.detector.detect_all(result)
        
        if remaining:
            print(f"   ⚠️ {len(remaining)}개 위반 남음")
        else:
            print("   ✅ 모든 위반 해결됨!")
        
        return result, violations, fix_log
    
    def _fix_violations_sequential(self, result: Dict, violations: List[Violation]) -> List[Dict]:
        """순차적으로 위반 수정"""
        fix_log = []
        
        # 깊은 경로부터 수정 (자식 먼저 수정해야 부모 수정 시 영향 없음)
        sorted_violations = sorted(violations, key=lambda v: len(v.path), reverse=True)
        
        for i, violation in enumerate(sorted_violations, 1):
            print(f"\n   [{i}/{len(sorted_violations)}] {violation.violation_type}")
            print(f"       노드: {violation.node_id}")
            
            # 컨텍스트 추출
            context = extract_context(result, violation)
            
            # 수정 적용
            fixed_node = self.fixer.fix_violation(context)
            
            if fixed_node:
                # 원본에 반영
                if set_node_by_path(result, violation.path, fixed_node):
                    print(f"       ✅ 수정 완료")
                    fix_log.append({
                        'violation': violation.violation_type,
                        'path': violation.path,
                        'node_id': violation.node_id,
                        'status': 'fixed'
                    })
                else:
                    print(f"       ❌ 수정 적용 실패")
                    fix_log.append({
                        'violation': violation.violation_type,
                        'path': violation.path,
                        'node_id': violation.node_id,
                        'status': 'failed'
                    })
            else:
                print(f"       ⚠️ 수정 생성 실패")
        
        return fix_log
    
    async def _fix_violations_parallel(self, result: Dict, violations: List[Violation]) -> List[Dict]:
        """병렬로 위반 수정 (depth별로 그룹화하여 같은 depth는 동시 처리)"""
        fix_log = []
        
        # depth별로 그룹화
        depth_groups: Dict[int, List[Violation]] = {}
        for v in violations:
            depth = len(v.path)
            if depth not in depth_groups:
                depth_groups[depth] = []
            depth_groups[depth].append(v)
        
        # 깊은 depth부터 처리 (자식 먼저)
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        for depth in sorted(depth_groups.keys(), reverse=True):
            group = depth_groups[depth]
            print(f"\n   Depth {depth}: {len(group)}개 위반 병렬 처리 중...")
            
            # 같은 depth의 위반들을 병렬로 처리
            tasks = []
            for violation in group:
                task = self._fix_single_violation_async(result, violation, semaphore)
                tasks.append(task)
            
            # 병렬 실행
            results = await asyncio.gather(*tasks)
            
            # 결과 적용 (순차적으로 - 동시 수정 방지)
            for violation, (fixed_node, status) in zip(group, results):
                if fixed_node and status == 'success':
                    if set_node_by_path(result, violation.path, fixed_node):
                        print(f"       ✅ {violation.node_id[:20]}... 수정 완료")
                        fix_log.append({
                            'violation': violation.violation_type,
                            'path': violation.path,
                            'node_id': violation.node_id,
                            'status': 'fixed'
                        })
                    else:
                        fix_log.append({
                            'violation': violation.violation_type,
                            'path': violation.path,
                            'node_id': violation.node_id,
                            'status': 'failed'
                        })
                else:
                    fix_log.append({
                        'violation': violation.violation_type,
                        'path': violation.path,
                        'node_id': violation.node_id,
                        'status': 'failed'
                    })
        
        return fix_log
    
    async def _fix_single_violation_async(self, result: Dict, violation: Violation,
                                          semaphore: asyncio.Semaphore) -> Tuple[Optional[Dict], str]:
        """단일 위반을 비동기로 수정"""
        async with semaphore:
            loop = asyncio.get_event_loop()
            
            try:
                # 컨텍스트 추출 (CPU 작업)
                context = extract_context(result, violation)
                
                # LLM 호출 (I/O 작업, ThreadPoolExecutor 사용)
                fixed_node = await loop.run_in_executor(
                    None,
                    lambda: self.fixer.fix_violation(context)
                )
                
                return (fixed_node, 'success') if fixed_node else (None, 'failed')
            
            except Exception as e:
                print(f"       ⚠️ {violation.node_id}: {e}")
                return (None, 'error')


# ============================================================
# CLI / Main
# ============================================================

def main():
    """메인 실행"""
    import os
    
    base_path = Path(__file__).parent
    
    input_path = base_path / INPUT_STRUCTURE
    image_path = base_path / INPUT_IMAGE if INPUT_IMAGE else None
    output_path = base_path / OUTPUT_FILE
    
    if not input_path.exists():
        print(f"❌ 입력 파일을 찾을 수 없습니다: {input_path}")
        return
    
    print(f"\n📋 설정:")
    print(f"   - 입력: {input_path}")
    print(f"   - 이미지: {image_path or '없음'}")
    print(f"   - 출력: {output_path}")
    
    # 데이터 로드
    with open(input_path, 'r', encoding='utf-8') as f:
        structure = json.load(f)
    
    # LLM 클라이언트 설정 (선택적)
    llm_client = None
    try:
        from openai import OpenAI
        api_key = os.getenv('OPENAI_API_KEY')
        if api_key:
            llm_client = OpenAI(api_key=api_key)
            print("   - LLM: OpenAI 사용")
        else:
            print("   - LLM: 규칙 기반만 사용 (API 키 없음)")
    except ImportError:
        print("   - LLM: 규칙 기반만 사용 (openai 패키지 없음)")
    
    # 병렬 처리 설정
    print(f"   - 병렬 처리: {'⚡ 활성화' if USE_PARALLEL else '❌ 비활성화'} (동시 {MAX_CONCURRENT}개)")
    
    # 검증 및 수정
    import time
    start_time = time.time()
    
    validator = StructureValidator(
        llm_client, 
        str(image_path) if image_path else None,
        use_parallel=USE_PARALLEL,
        max_concurrent=MAX_CONCURRENT
    )
    fixed_structure, violations, fix_log = validator.validate_and_fix(structure)
    
    elapsed = time.time() - start_time
    
    # 결과 저장
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(fixed_structure, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 결과 저장: {output_path}")
    
    # 요약
    print(f"\n📊 요약:")
    print(f"   - 발견된 위반: {len(violations)}개")
    print(f"   - 수정 성공: {sum(1 for l in fix_log if l['status'] == 'fixed')}개")
    print(f"   - 수정 실패: {sum(1 for l in fix_log if l['status'] == 'failed')}개")
    print(f"   - ⏱️ 소요 시간: {elapsed:.1f}초")


if __name__ == "__main__":
    main()
