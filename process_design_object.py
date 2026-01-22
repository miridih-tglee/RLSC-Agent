#!/usr/bin/env python3
"""
Design Object 처리 파이프라인

1. DB에서 design_object 데이터 가져오기
2. 썸네일 다운로드 (WebP → PNG)
3. structure_json 수정 (겹침 수정, padding/gap 계산)
4. 모든 파일 저장

사용법:
    # 단일 ID
    python process_design_object.py 283782
    
    # 여러 ID (쉼표 또는 공백으로 구분)
    python process_design_object.py 283782,283725,277457
    python process_design_object.py 283782 283725 277457
    
    # 폴더 경로 (폴더 내 디렉토리명을 ID로 사용)
    python process_design_object.py --dir /path/to/folder
    
    # 출력 폴더 지정
    python process_design_object.py --dir /path/to/folder --output /path/to/output
"""

import sys
import json
import uuid as uuid_lib
import httpx
from pathlib import Path
from copy import deepcopy
from typing import Dict, List, Tuple, Optional
from PIL import Image
from io import BytesIO

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2가 설치되어 있지 않습니다. 설치해주세요:")
    print("  pip install psycopg2-binary")
    sys.exit(1)


# ============================================================
# DB 설정
# ============================================================
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54322,
    "user": "postgres",
    "password": "postgres",
    "dbname": "postgres"
}

COLUMNS = [
    "id",
    "uuid",
    "origin_size_thumbnail_url",
    "structure_json",
    "content_signature",
    "content_signature_sorted",
    "design_object_meta"
]


# ============================================================
# DB 함수
# ============================================================
def fetch_design_object(object_id: int) -> dict:
    """DB에서 design_object 데이터 조회"""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = f"""
                SELECT {', '.join(COLUMNS)}
                FROM design_objects
                WHERE id = %s
            """
            cur.execute(query, (object_id,))
            result = cur.fetchone()
            return dict(result) if result else None
    finally:
        conn.close()


# ============================================================
# 파일 저장 함수
# ============================================================
def download_thumbnail(url: str, output_path: Path) -> bool:
    """썸네일 다운로드 (webp -> png, 투명 배경은 흰색으로)"""
    if not url:
        print("  ⚠️  썸네일 URL이 없습니다.")
        return False
    
    try:
        print(f"  📥 다운로드 중: {url[:60]}...")
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        
        image = Image.open(BytesIO(response.content))
        
        if image.mode == "RGBA":
            white_bg = Image.new("RGB", image.size, (255, 255, 255))
            white_bg.paste(image, mask=image.split()[3])
            image = white_bg
            print("  🎨 투명 배경 → 흰색 배경")
        elif image.mode != "RGB":
            image = image.convert("RGB")
        
        image.save(output_path, "PNG")
        print(f"  ✅ {output_path.name} ({image.size[0]}x{image.size[1]})")
        return True
    except Exception as e:
        print(f"  ❌ 다운로드 실패: {e}")
        return False


def save_json(data, output_path: Path, name: str) -> None:
    """JSON 파일 저장"""
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음")
        return
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {output_path.name}")


def save_text(data: str, output_path: Path, name: str) -> None:
    """텍스트 파일 저장"""
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음")
        return
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(data)
    print(f"  ✅ {output_path.name}")


# ============================================================
# Structure Fixer 유틸리티
# ============================================================
def generate_id() -> str:
    return str(uuid_lib.uuid4())


def get_role(node: Dict) -> str:
    role = node.get('role', '')
    return role.split('.')[-1] if '.' in role else role


def get_type(node: Dict) -> str:
    return node.get('type', '')


def is_background(node: Dict) -> bool:
    return get_role(node) == 'Background'


def is_decoration(node: Dict) -> bool:
    return 'Element.Decoration' in node.get('role', '')


def is_marker(node: Dict) -> bool:
    return get_role(node) == 'Marker'


def is_frame(node: Dict) -> bool:
    return get_type(node) == 'Frame'


def is_image(node: Dict) -> bool:
    return get_type(node) == 'Image'


def get_bbox(node: Dict) -> Optional[Tuple[float, float, float, float]]:
    pos = node.get('position', {})
    if not pos:
        return None
    x, y = pos.get('x', 0), pos.get('y', 0)
    w, h = pos.get('width', 0), pos.get('height', 0)
    return (x, y, x + w, y + h)


def get_area(node: Dict) -> float:
    pos = node.get('position', {})
    return pos.get('width', 0) * pos.get('height', 0)


def is_overlapping(bbox1: Tuple, bbox2: Tuple, threshold: float = 0.1) -> bool:
    x1 = max(bbox1[0], bbox2[0])
    y1 = max(bbox1[1], bbox2[1])
    x2 = min(bbox1[2], bbox2[2])
    y2 = min(bbox1[3], bbox2[3])
    
    if x1 >= x2 or y1 >= y2:
        return False
    
    intersection = (x2 - x1) * (y2 - y1)
    area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
    area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
    smaller_area = min(area1, area2)
    
    if smaller_area <= 0:
        return False
    
    return intersection / smaller_area > threshold


# ============================================================
# 겹침 검사
# ============================================================
def should_check_pair(node1: Dict, node2: Dict) -> bool:
    """겹침 검사 대상인지 확인 (컨테이너 타입 제외)"""
    role1, role2 = get_role(node1), get_role(node2)
    type1, type2 = get_type(node1), get_type(node2)
    
    if role1 == 'Background' or role2 == 'Background':
        return False
    
    # 컨테이너 타입은 겹침 검사 제외 (무한 재귀 방지)
    container_types = ['Group', 'HStack', 'VStack', 'ZStack', 'Grid']
    if type1 in container_types or type2 in container_types:
        return False
    
    if role1 in ['Title', 'Description', 'Subtitle'] or type1 == 'Text':
        return False
    if role2 in ['Title', 'Description', 'Subtitle'] or type2 == 'Text':
        return False
    
    if is_frame(node1) or is_frame(node2) or is_image(node1) or is_image(node2):
        return False
    
    if role1 == 'Decoration' and role2 == 'Decoration':
        return True
    if (role1 == 'Decoration' and role2 == 'Marker') or (role1 == 'Marker' and role2 == 'Decoration'):
        return True
    if role1 == 'Marker' and role2 == 'Marker':
        return True
    
    return False


def find_overlapping_pairs(children: List[Dict]) -> List[Tuple[int, int]]:
    pairs = []
    for i in range(len(children)):
        bbox_i = get_bbox(children[i])
        if not bbox_i:
            continue
        for j in range(i + 1, len(children)):
            bbox_j = get_bbox(children[j])
            if not bbox_j:
                continue
            if should_check_pair(children[i], children[j]) and is_overlapping(bbox_i, bbox_j):
                pairs.append((i, j))
    return pairs


def group_overlapping(children: List[Dict], pairs: List[Tuple[int, int]]) -> List[List[int]]:
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
    
    groups = {}
    for i, j in pairs:
        for idx in [i, j]:
            root = find(idx)
            if root not in groups:
                groups[root] = set()
            groups[root].add(idx)
    
    return [list(g) for g in groups.values() if len(g) >= 2]


# ============================================================
# 그룹 묶기
# ============================================================
def wrap_in_group(nodes: List[Dict]) -> Dict:
    """
    겹치는 노드들을 Group으로 묶기
    - Text, Frame, Image 타입은 Background가 될 수 없음
    - SVG 중 가장 큰 요소가 Background가 됨
    """
    if not nodes:
        return {}
    
    # Text, Frame, Image가 아닌 노드 중 가장 큰 것을 Background로 선정
    max_area, bg_idx = -1, -1
    for i, node in enumerate(nodes):
        node_type = get_type(node)
        # Text, Frame, Image 타입은 Background 후보에서 제외
        if node_type in ['Text', 'Frame', 'Image']:
            continue
        area = get_area(node)
        if area > max_area:
            max_area, bg_idx = area, i
    
    all_bboxes = [get_bbox(n) for n in nodes if get_bbox(n)]
    if all_bboxes:
        min_x = min(b[0] for b in all_bboxes)
        min_y = min(b[1] for b in all_bboxes)
        max_x = max(b[2] for b in all_bboxes)
        max_y = max(b[3] for b in all_bboxes)
    else:
        min_x = min_y = 0
        max_x = max_y = 100
    
    wrapped_children = []
    for i, node in enumerate(nodes):
        node_copy = deepcopy(node)
        if i == bg_idx and bg_idx >= 0:  # bg_idx가 유효한 경우에만
            node_copy['role'] = 'Role.Element.Background'
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


def fix_multiple_backgrounds(children: List[Dict]) -> List[Dict]:
    backgrounds = [i for i, c in enumerate(children) if is_background(c)]
    if len(backgrounds) <= 1:
        return children
    
    bg_areas = [(i, get_area(children[i])) for i in backgrounds]
    bg_areas.sort(key=lambda x: x[1], reverse=True)
    largest_bg_idx = bg_areas[0][0]
    
    result = []
    for i, child in enumerate(children):
        child_copy = deepcopy(child)
        if i in backgrounds and i != largest_bg_idx:
            child_copy['role'] = 'Role.Element.Decoration'
        result.append(child_copy)
    
    return result


def find_background_candidate(children: List[Dict]) -> int:
    """
    Background 후보 찾기 (가장 큰 Decoration 또는 Marker)
    - Text, Frame, Image 타입은 Background가 될 수 없음
    - SVG 타입만 Background 후보
    """
    max_area, max_idx = -1, -1
    for i, child in enumerate(children):
        node_type = get_type(child)
        # Text, Frame, Image 타입은 Background 후보에서 제외
        if node_type in ['Text', 'Frame', 'Image']:
            continue
        # 이미 Background면 제외
        if is_background(child):
            continue
        # Decoration 또는 Marker만 후보
        role = get_role(child)
        if role in ['Decoration', 'Marker']:
            area = get_area(child)
            if area > max_area:
                max_area, max_idx = area, i
    return max_idx


# ============================================================
# 메인 수정 함수
# ============================================================
MAX_RECURSION_DEPTH = 50  # 최대 재귀 깊이 제한

def fix_node(node: Dict, depth: int = 0, verbose: bool = True) -> Dict:
    # 재귀 깊이 제한 체크
    if depth > MAX_RECURSION_DEPTH:
        if verbose:
            print(f"⚠️ 최대 재귀 깊이 초과 (depth={depth}), 더 이상 처리하지 않음")
        return deepcopy(node)
    
    indent = "    " * min(depth, 10)  # 들여쓰기 제한
    result = deepcopy(node)
    children = result.get('children', [])
    
    if not children:
        return result
    
    if verbose:
        node_id = node.get('id', 'unknown')[:20]
        print(f"{indent}📁 {node_id} ({get_type(node)})")
    
    # 1. 자식들 먼저 재귀 처리
    children = [fix_node(c, depth + 1, verbose) for c in children]
    
    # 2. Background 중복 수정
    children = fix_multiple_backgrounds(children)
    
    # 3. 먼저 겹침 검사
    pairs = find_overlapping_pairs(children)
    
    # 4. 겹침이 있을 때만! 가장 큰 Decoration → Background
    if pairs:
        bg_idx = find_background_candidate(children)
        if bg_idx >= 0:
            children[bg_idx] = deepcopy(children[bg_idx])
            children[bg_idx]['role'] = 'Role.Element.Background'
            if verbose:
                print(f"{indent}   🎨 겹침 발견 → 가장 큰 Deco → BG")
        
        # 5. 다시 겹침 검사 (Background 제외됨)
        pairs = find_overlapping_pairs(children)
        
        # 6. 아직 겹치면 Group으로 묶기
        if pairs:
            groups = group_overlapping(children, pairs)
            if groups:
                grouped = set()
                for g in groups:
                    grouped.update(g)
                
                new_children = [c for i, c in enumerate(children) if i not in grouped]
                for group_indices in groups:
                    group_nodes = [children[i] for i in group_indices]
                    new_group = wrap_in_group(group_nodes)
                    # ✅ 새로 생성된 Group도 재귀적으로 fix!
                    new_group = fix_node(new_group, depth + 1, verbose)
                    new_children.append(new_group)
                    if verbose:
                        print(f"{indent}   📦 Group 생성 및 내부 수정: {len(group_nodes)}개")
                
                children = new_children
    
    # 7. Background 중복 수정
    children = fix_multiple_backgrounds(children)
    result['children'] = children
    return result


# ============================================================
# 좌표 변환
# ============================================================
def to_absolute_coords(node: Dict, parent_x: float = 0, parent_y: float = 0) -> Dict:
    result = deepcopy(node)
    pos = result.get('position', {})
    
    if pos:
        abs_x = parent_x + pos.get('x', 0)
        abs_y = parent_y + pos.get('y', 0)
        pos['x'], pos['y'] = abs_x, abs_y
    else:
        abs_x, abs_y = parent_x, parent_y
    
    children = result.get('children', [])
    if children:
        result['children'] = [to_absolute_coords(c, abs_x, abs_y) for c in children]
    
    return result


def to_relative_coords(node: Dict, parent_x: float = 0, parent_y: float = 0) -> Dict:
    result = deepcopy(node)
    pos = result.get('position', {})
    
    if pos:
        abs_x, abs_y = pos.get('x', 0), pos.get('y', 0)
        pos['x'] = round(abs_x - parent_x, 2)
        pos['y'] = round(abs_y - parent_y, 2)
    else:
        abs_x, abs_y = parent_x, parent_y
    
    children = result.get('children', [])
    if children:
        result['children'] = [to_relative_coords(c, abs_x, abs_y) for c in children]
    
    return result


# ============================================================
# Layout Properties
# ============================================================
def add_layout_properties(node: Dict) -> Dict:
    result = deepcopy(node)
    node_type = get_type(result)
    
    if node_type == 'HStack':
        result['direction'] = 'horizontal'
    elif node_type == 'VStack':
        result['direction'] = 'vertical'
    
    children = result.get('children', [])
    if not children:
        return result
    
    result['children'] = [add_layout_properties(c) for c in children]
    children = result['children']
    
    parent_pos = result.get('position', {})
    parent_w, parent_h = parent_pos.get('width', 0), parent_pos.get('height', 0)
    
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
    
    if len(children) >= 2 and node_type in ['HStack', 'VStack']:
        gaps = []
        key = 'x' if node_type == 'HStack' else 'y'
        sorted_children = sorted(children, key=lambda c: c.get('position', {}).get(key, 0))
        
        for i in range(len(sorted_children) - 1):
            bbox1, bbox2 = get_bbox(sorted_children[i]), get_bbox(sorted_children[i + 1])
            if bbox1 and bbox2:
                gap = (bbox2[0] - bbox1[2]) if node_type == 'HStack' else (bbox2[1] - bbox1[3])
                if gap > 0:
                    gaps.append(gap)
        
        if gaps:
            result['gap'] = round(sum(gaps) / len(gaps), 2)
    
    return result


# ============================================================
# Structure 수정 파이프라인
# ============================================================
def fix_structure(structure: Dict, verbose: bool = True) -> Dict:
    """structure_json 수정 파이프라인"""
    if verbose:
        print("\n  🔄 절대좌표 변환")
    structure_abs = to_absolute_coords(structure)
    
    if verbose:
        print("  🔧 겹침 수정")
    fixed_abs = fix_node(structure_abs, verbose=verbose)
    
    if verbose:
        print("  🔄 상대좌표 변환")
    fixed_rel = to_relative_coords(fixed_abs)
    
    if verbose:
        print("  📐 padding/gap/direction 추가")
    result = add_layout_properties(fixed_rel)
    
    return result


# ============================================================
# 단일 오브젝트 처리
# ============================================================
def process_single_object(object_id: int, output_base_dir: Path = None, verbose: bool = True) -> bool:
    """
    단일 Design Object 처리
    
    Args:
        object_id: 처리할 디자인 오브젝트 ID
        output_base_dir: 출력 기본 폴더 (None이면 기본 data 폴더 사용)
        verbose: 상세 로그 출력 여부
    
    Returns:
        성공 여부
    """
    if verbose:
        print("=" * 60)
        print(f"🚀 Design Object 처리: ID={object_id}")
        print("=" * 60)
    
    # 1. DB에서 데이터 조회
    if verbose:
        print(f"\n📥 Step 1: DB에서 데이터 조회")
    data = fetch_design_object(object_id)
    
    if not data:
        print(f"  ❌ id={object_id}에 해당하는 데이터를 찾을 수 없습니다.")
        return False
    
    if verbose:
        print(f"  ✅ 데이터 찾음! uuid: {data.get('uuid')}")
    
    # 2. 출력 폴더 생성
    if output_base_dir is None:
        output_base_dir = Path(__file__).parent / "data"
    output_dir = output_base_dir / str(object_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"\n📁 출력 폴더: {output_dir}")
    
    # 3. 썸네일 다운로드
    if verbose:
        print("\n📷 Step 2: 썸네일 다운로드")
    download_thumbnail(
        data.get("origin_size_thumbnail_url"),
        output_dir / "thumbnail.png"
    )
    
    # 4. JSON 파일들 저장
    if verbose:
        print("\n📄 Step 3: 원본 파일 저장")
    save_json(data.get("structure_json"), output_dir / "structure_json.json", "structure_json")
    save_json(data.get("content_signature"), output_dir / "content_signature.json", "content_signature")
    save_text(data.get("content_signature_sorted"), output_dir / "content_signature_sorted.txt", "content_signature_sorted")
    save_json(data.get("design_object_meta"), output_dir / "design_object_meta.json", "design_object_meta")
    
    uuid_data = {"uuid": str(data.get("uuid")) if data.get("uuid") else None, "id": object_id}
    save_json(uuid_data, output_dir / "info.json", "info")
    
    # 5. Structure 수정
    if verbose:
        print("\n🔧 Step 4: Structure 수정")
    structure = data.get("structure_json")
    
    if structure:
        fixed_structure = fix_structure(structure, verbose=verbose)
        
        # 6. 수정된 Structure 저장
        if verbose:
            print("\n💾 Step 5: 수정된 Structure 저장")
        save_json(fixed_structure, output_dir / "structure_json_fixed.json", "structure_json_fixed")
    else:
        print("  ⚠️ structure_json이 없어서 수정을 건너뜁니다.")
    
    if verbose:
        print(f"\n🎉 완료! ID={object_id}")
    
    return True


def process_multiple_objects(object_ids: List[int], output_base_dir: Path = None, verbose: bool = False) -> Dict:
    """
    여러 Design Object 처리
    
    Args:
        object_ids: 처리할 디자인 오브젝트 ID 리스트
        output_base_dir: 출력 기본 폴더
        verbose: 각 오브젝트 상세 로그 출력 여부
    
    Returns:
        처리 결과 통계
    """
    total = len(object_ids)
    success = 0
    failed = []
    
    print("=" * 60)
    print(f"🚀 Design Object 일괄 처리")
    print(f"   총 {total}개 ID 처리 예정")
    if output_base_dir:
        print(f"   출력 폴더: {output_base_dir}")
    print("=" * 60)
    
    for i, object_id in enumerate(object_ids, 1):
        print(f"\n[{i}/{total}] Processing ID: {object_id}")
        try:
            result = process_single_object(object_id, output_base_dir, verbose=verbose)
            if result:
                success += 1
                print(f"  ✅ 성공")
            else:
                failed.append(object_id)
                print(f"  ❌ 실패")
        except Exception as e:
            failed.append(object_id)
            print(f"  ❌ 오류: {e}")
    
    print("\n" + "=" * 60)
    print(f"📊 처리 완료!")
    print(f"   성공: {success}/{total}")
    print(f"   실패: {len(failed)}/{total}")
    if failed:
        print(f"   실패한 ID: {failed[:10]}{'...' if len(failed) > 10 else ''}")
    print("=" * 60)
    
    return {
        'total': total,
        'success': success,
        'failed': failed
    }


def get_ids_from_directory(dir_path: Path) -> List[int]:
    """디렉토리 내 폴더명에서 ID 추출"""
    ids = []
    if not dir_path.exists():
        print(f"❌ 디렉토리를 찾을 수 없습니다: {dir_path}")
        return ids
    
    for item in dir_path.iterdir():
        if item.is_dir():
            try:
                ids.append(int(item.name))
            except ValueError:
                print(f"  ⚠️ '{item.name}'은(는) 숫자가 아니므로 건너뜁니다.")
    
    ids.sort()
    return ids


def parse_id_list(args: List[str]) -> List[int]:
    """인자에서 ID 리스트 파싱 (쉼표, 공백 구분 지원)"""
    ids = []
    for arg in args:
        # 쉼표로 구분된 경우
        parts = arg.replace(',', ' ').split()
        for part in parts:
            try:
                ids.append(int(part))
            except ValueError:
                print(f"  ⚠️ '{part}'은(는) 숫자가 아니므로 건너뜁니다.")
    return ids


# ============================================================
# 메인
# ============================================================
def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Design Object 처리 파이프라인',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 단일 ID
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
        """
    )
    
    parser.add_argument('ids', nargs='*', help='처리할 디자인 오브젝트 ID (쉼표 또는 공백으로 구분)')
    parser.add_argument('--dir', '-d', type=str, help='ID를 추출할 디렉토리 경로')
    parser.add_argument('--output', '-o', type=str, help='출력 디렉토리 경로')
    parser.add_argument('--verbose', '-v', action='store_true', help='상세 로그 출력')
    
    args = parser.parse_args()
    
    # 출력 디렉토리 설정
    output_dir = Path(args.output) if args.output else None
    
    # ID 수집
    object_ids = []
    
    # 1. --dir 옵션으로 폴더에서 ID 추출
    if args.dir:
        dir_path = Path(args.dir)
        print(f"📂 디렉토리에서 ID 추출: {dir_path}")
        object_ids = get_ids_from_directory(dir_path)
        print(f"  → {len(object_ids)}개 ID 발견")
    
    # 2. 인자로 전달된 ID
    elif args.ids:
        object_ids = parse_id_list(args.ids)
    
    # ID가 없으면 도움말 출력
    if not object_ids:
        parser.print_help()
        sys.exit(1)
    
    # 처리
    if len(object_ids) == 1:
        # 단일 ID 처리
        success = process_single_object(object_ids[0], output_dir, verbose=True)
        sys.exit(0 if success else 1)
    else:
        # 여러 ID 처리
        result = process_multiple_objects(object_ids, output_dir, verbose=args.verbose)
        sys.exit(0 if result['failed'] == [] else 1)


if __name__ == "__main__":
    main()
