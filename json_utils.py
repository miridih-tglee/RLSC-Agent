"""
JSON 유틸리티 함수들
대용량 JSON 파일을 효율적으로 처리하기 위한 헬퍼 함수들
"""

import json
import sys
from typing import Dict, List, Optional, Set, Any
from copy import deepcopy


# simplified_structure에 포함할 필드들
SIMPLIFIED_KEEP_FIELDS = {
    'id', 'role', 'type', 'children', 'position', 'content',
    'direction', 'gap', 'alignment', 'padding',
    'verticalAlignment', 'horizontalAlignment',
    'verticalGap', 'horizontalGap'
}


def create_simplified_structure(raw_data: Dict) -> Dict:
    """
    raw_data에서 simplified_structure 생성
    
    필요한 필드만 추출하여 단순화된 구조 생성:
    - id, role, type, children (구조)
    - position (겹침 감지용)
    - content (텍스트 요소)
    - direction, gap, alignment 등 (레이아웃)
    
    제거되는 필드:
    - svgData, url (리소스 참조)
    - areaSize, fontSize, fontFamily, fontWeight (스타일 상세)
    - wrap, maxWidth 등 (부가 정보)
    
    Args:
        raw_data: 원본 raw_data JSON
    
    Returns:
        단순화된 구조
    """
    def simplify_node(node: Dict) -> Dict:
        """노드를 단순화"""
        simplified = {}
        
        # 필요한 필드만 복사
        for key, value in node.items():
            if key in SIMPLIFIED_KEEP_FIELDS:
                if key == 'children' and isinstance(value, list):
                    # children은 재귀적으로 처리
                    simplified['children'] = [simplify_node(child) for child in value]
                elif key == 'content' and value:
                    # content는 앞부분만 (너무 길면 자름)
                    simplified['content'] = value[:200] if len(value) > 200 else value
                else:
                    simplified[key] = deepcopy(value)
        
        return simplified
    
    return simplify_node(raw_data)


def save_simplified_structure(raw_data_path: str, output_path: str = None) -> str:
    """
    raw_data.json에서 simplified_structure.json 생성 및 저장
    
    Args:
        raw_data_path: raw_data.json 파일 경로
        output_path: 출력 파일 경로 (None이면 자동 생성)
    
    Returns:
        저장된 파일 경로
    """
    import os
    
    # 출력 경로 결정
    if output_path is None:
        dir_path = os.path.dirname(raw_data_path)
        output_path = os.path.join(dir_path, 'simplified_structure.json')
    
    # raw_data 로드
    with open(raw_data_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
    
    # simplified_structure 생성
    simplified = create_simplified_structure(raw_data)
    
    # 저장
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(simplified, f, indent=2, ensure_ascii=False)
    
    print(f"✅ simplified_structure 생성 완료: {output_path}")
    return output_path


def find_node_by_id(node: Dict, target_id: str) -> Optional[Dict]:
    """
    트리에서 특정 id를 가진 노드를 찾아 반환
    
    Args:
        node: 검색할 노드 (트리 루트)
        target_id: 찾을 노드의 id
    
    Returns:
        찾은 노드 또는 None
    """
    if node.get('id') == target_id:
        return node
    
    for child in node.get('children', []):
        result = find_node_by_id(child, target_id)
        if result:
            return result
    
    return None


def extract_subtree(node: Dict, target_id: str, include_siblings: bool = False) -> Optional[Dict]:
    """
    특정 id를 가진 노드와 그 하위 트리만 추출
    
    Args:
        node: 전체 트리
        target_id: 추출할 노드의 id
        include_siblings: 형제 노드도 포함할지 여부
    
    Returns:
        추출된 서브트리 또는 None
    """
    found_node = find_node_by_id(node, target_id)
    if not found_node:
        return None
    
    # 깊은 복사로 서브트리 반환
    subtree = deepcopy(found_node)
    return subtree


def extract_node_group(node: Dict, target_ids: Set[str], 
                      include_parents: bool = True,
                      include_children: bool = True) -> Dict:
    """
    여러 id에 해당하는 노드 그룹을 추출
    관련된 부모/자식 노드도 포함할 수 있음
    
    Args:
        node: 전체 트리
        target_ids: 추출할 노드 id들의 집합
        include_parents: 부모 노드도 포함할지 여부
        include_children: 자식 노드도 포함할지 여부
    
    Returns:
        추출된 노드 그룹 (트리 구조 유지)
    """
    def collect_related_nodes(current_node: Dict, collected: Set[str]) -> Set[str]:
        """관련된 노드 id들을 수집"""
        node_id = current_node.get('id')
        if not node_id:
            return collected
        
        # 현재 노드가 타겟이면 수집
        if node_id in target_ids:
            collected.add(node_id)
            
            # 부모 포함 옵션
            if include_parents:
                # 부모를 찾아서 추가 (재귀적으로)
                pass  # 부모는 상위에서 처리
            
            # 자식 포함 옵션
            if include_children:
                for child in current_node.get('children', []):
                    child_id = child.get('id')
                    if child_id:
                        collected.add(child_id)
        
        # 자식 노드 재귀 처리
        for child in current_node.get('children', []):
            collected = collect_related_nodes(child, collected)
        
        return collected
    
    # 관련 노드 id 수집
    related_ids = collect_related_nodes(node, set())
    
    # 수집된 노드들로 트리 재구성
    def build_tree(current_node: Dict) -> Optional[Dict]:
        """관련 노드만 포함하는 트리 재구성"""
        node_id = current_node.get('id')
        
        # 현재 노드가 관련 노드에 포함되거나, 자식 중에 관련 노드가 있으면 포함
        has_related_children = False
        filtered_children = []
        
        for child in current_node.get('children', []):
            child_tree = build_tree(child)
            if child_tree:
                filtered_children.append(child_tree)
                has_related_children = True
        
        # 현재 노드가 타겟이거나 관련 자식이 있으면 포함
        if node_id in related_ids or has_related_children:
            result = deepcopy(current_node)
            result['children'] = filtered_children
            return result
        
        return None
    
    return build_tree(node) or {}


def get_node_context(node: Dict, target_id: str, 
                    context_depth: int = 2) -> Dict:
    """
    특정 노드의 컨텍스트를 추출
    부모, 형제, 자식을 포함한 관련 노드 그룹 반환
    
    Args:
        node: 전체 트리
        target_id: 타겟 노드 id
        context_depth: 포함할 컨텍스트 깊이 (부모/자식 레벨)
    
    Returns:
        컨텍스트가 포함된 노드 그룹
    """
    def find_with_parents(current_node: Dict, target_id: str, 
                          path: List[Dict] = None) -> Optional[List[Dict]]:
        """노드를 찾고 부모 경로도 함께 반환"""
        if path is None:
            path = []
        
        current_path = path + [current_node]
        
        if current_node.get('id') == target_id:
            return current_path
        
        for child in current_node.get('children', []):
            result = find_with_parents(child, target_id, current_path)
            if result:
                return result
        
        return None
    
    # 타겟 노드와 부모 경로 찾기
    path = find_with_parents(node, target_id)
    if not path:
        return {}
    
    # 타겟 노드
    target_node = path[-1]
    
    # 컨텍스트 구성
    context = {
        'target': deepcopy(target_node),
        'parents': [deepcopy(p) for p in path[:-1][-context_depth:]],  # 최근 부모들만
        'siblings': [],
        'children': deepcopy(target_node.get('children', []))[:context_depth]  # 직접 자식만
    }
    
    # 형제 노드 찾기
    if len(path) > 1:
        parent = path[-2]
        siblings = parent.get('children', [])
        target_index = next((i for i, s in enumerate(siblings) if s.get('id') == target_id), -1)
        if target_index >= 0:
            # 형제들 (앞뒤 몇 개만)
            start = max(0, target_index - 1)
            end = min(len(siblings), target_index + 2)
            context['siblings'] = [deepcopy(s) for s in siblings[start:end]]
    
    return context


def prepare_llm_context(node: Dict, target_id: str, 
                        max_nodes: int = 50) -> Dict:
    """
    LLM에 전달할 컨텍스트 준비
    관련 노드만 포함하여 토큰 수를 제한
    
    Args:
        node: 전체 트리
        target_id: 타겟 노드 id
        max_nodes: 최대 포함할 노드 수
    
    Returns:
        LLM에 전달할 최적화된 컨텍스트
    """
    # 타겟 노드 찾기
    target_node = find_node_by_id(node, target_id)
    if not target_node:
        return {}
    
    # 관련 노드 수집 (BFS 방식으로 제한)
    collected = []
    queue = [(target_node, 0)]  # (node, depth)
    visited = {target_id}
    
    while queue and len(collected) < max_nodes:
        current, depth = queue.pop(0)
        node_id = current.get('id')
        
        # 노드 정보 추출 (필요한 필드만)
        node_info = {
            'id': node_id,
            'role': current.get('role'),
            'type': current.get('type'),
            'depth': depth
        }
        
        # 중요한 속성만 포함
        if 'position' in current:
            node_info['position'] = current['position']
        if 'alignment' in current:
            node_info['alignment'] = current['alignment']
        if 'direction' in current:
            node_info['direction'] = current['direction']
        
        collected.append(node_info)
        
        # 자식 노드 추가 (깊이 제한)
        if depth < 3:  # 최대 3단계 깊이
            for child in current.get('children', []):
                child_id = child.get('id')
                if child_id and child_id not in visited:
                    visited.add(child_id)
                    queue.append((child, depth + 1))
    
    # 부모 노드도 추가
    def get_parent_path(current_node: Dict, target_id: str, path: List[Dict] = None) -> List[Dict]:
        if path is None:
            path = []
        
        if current_node.get('id') == target_id:
            return path
        
        for child in current_node.get('children', []):
            result = get_parent_path(child, target_id, path + [current_node])
            if result is not None:
                return result
        
        return None
    
    parent_path = get_parent_path(node, target_id)
    if parent_path:
        for parent in parent_path[-2:]:  # 최근 부모 2개만
            parent_info = {
                'id': parent.get('id'),
                'role': parent.get('role'),
                'type': parent.get('type'),
                'direction': parent.get('direction')
            }
            if parent_info['id'] not in [n['id'] for n in collected]:
                collected.insert(0, parent_info)
    
    return {
        'target_id': target_id,
        'nodes': collected,
        'total_nodes': len(collected)
    }


def load_json_partial(file_path: str, target_id: Optional[str] = None) -> Dict:
    """
    JSON 파일의 일부만 로드
    target_id가 지정되면 해당 노드와 관련 부분만 로드
    
    Args:
        file_path: JSON 파일 경로
        target_id: 로드할 특정 노드 id (None이면 전체 로드)
    
    Returns:
        로드된 JSON 데이터
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if target_id:
        # 특정 노드만 추출
        subtree = extract_subtree(data, target_id)
        return subtree if subtree else data
    
    return data


def save_json_incremental(data: Dict, file_path: str, target_id: str):
    """
    JSON 파일을 증분 방식으로 저장
    특정 노드만 업데이트 (대용량 파일에 유용)
    
    Args:
        data: 업데이트할 노드 데이터
        file_path: 저장할 파일 경로
        target_id: 업데이트할 노드 id
    """
    # 기존 파일 읽기
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    except FileNotFoundError:
        existing_data = data
    
    # 노드 업데이트
    def update_node(current: Dict, target_id: str, new_data: Dict) -> bool:
        if current.get('id') == target_id:
            current.update(new_data)
            return True
        
        for child in current.get('children', []):
            if update_node(child, target_id, new_data):
                return True
        
        return False
    
    update_node(existing_data, target_id, data)
    
    # 저장
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, indent=2, ensure_ascii=False)


# CLI 인터페이스
if __name__ == '__main__':
    """
    사용법:
        python json_utils.py create-simplified data/raw_data.json
        python json_utils.py create-simplified data/raw_data.json -o data/simplified_structure.json
    """
    if len(sys.argv) < 2:
        print("사용법:")
        print("  python json_utils.py create-simplified <raw_data_path> [-o output_path]")
        print("")
        print("예시:")
        print("  python json_utils.py create-simplified data/raw_data.json")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == 'create-simplified':
        if len(sys.argv) < 3:
            print("❌ raw_data 파일 경로를 지정해주세요.")
            sys.exit(1)
        
        raw_data_path = sys.argv[2]
        output_path = None
        
        # -o 옵션 처리
        if '-o' in sys.argv:
            idx = sys.argv.index('-o')
            if idx + 1 < len(sys.argv):
                output_path = sys.argv[idx + 1]
        
        try:
            save_simplified_structure(raw_data_path, output_path)
        except FileNotFoundError:
            print(f"❌ 파일을 찾을 수 없습니다: {raw_data_path}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"❌ JSON 파싱 오류: {e}")
            sys.exit(1)
    else:
        print(f"❌ 알 수 없는 명령: {command}")
        print("사용 가능한 명령: create-simplified")
        sys.exit(1)
