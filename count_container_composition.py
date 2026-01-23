#!/usr/bin/env python3
"""
ZStack/Group 내부 구성 분석

케이스 1: ZStack/Group 안에 요소(SVG, Image, Frame) + VStack/HStack이 함께 있는 경우
케이스 2: ZStack 안에 VStack/HStack 같은 컨테이너가 없고 element 요소만 있는 경우

※ Grid가 포함된 structure는 분석에서 제외
※ Frame과 겹치는 요소가 있는 structure는 분석에서 제외 (옵션: EXCLUDE_FRAME_OVERLAP)
"""

import sys
import json
import time
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from multiprocessing import Pool, cpu_count

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2가 설치되어 있지 않습니다.")
    print("  pip install psycopg2-binary")
    sys.exit(1)


# ============================================================
# 설정
# ============================================================
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54322,
    "user": "postgres",
    "password": "postgres",
    "dbname": "postgres"
}

BATCH_SIZE = 5000
NUM_WORKERS = max(1, cpu_count() - 1)

MIN_DEPTH = 4
MAX_DEPTH = 8

EXCLUDED_ROLES = [
    'Role.Page.Opening',
    'Role.Page.Agenda',
    'Role.Page.SectionDivider',
    'Role.Page.Ending',
    'Role.Page.Content'
]

EXCLUDED_STRUCTURE_ROLE_PREFIX = 'Role.LayoutContainer.Page'

# 분석 대상 타입
ELEMENT_TYPES = ['SVG', 'Image', 'Frame', 'Text']  # 요소 타입
LAYOUT_CONTAINER_TYPES = ['VStack', 'HStack']  # 레이아웃 컨테이너
ALL_CONTAINER_TYPES = ['VStack', 'HStack', 'ZStack', 'Group', 'Grid']  # 모든 컨테이너

# 제외 옵션
EXCLUDE_FRAME_OVERLAP = True  # Frame과 겹치는 요소가 있으면 제외


# ============================================================
# 헬퍼 함수들
# ============================================================
def get_type(node: Dict) -> str:
    return node.get('type', '')


def has_excluded_structure_role(structure_json) -> bool:
    """structure_json에서 제외 대상 role 패턴이 있는지 확인"""
    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return False

    if structure_json is None:
        return False

    def check_node(node):
        if isinstance(node, dict):
            role = node.get('role', '')
            if isinstance(role, str) and role.startswith(EXCLUDED_STRUCTURE_ROLE_PREFIX):
                return True

            children = node.get('children', [])
            for child in children:
                if check_node(child):
                    return True

        elif isinstance(node, list):
            for item in node:
                if check_node(item):
                    return True

        return False

    return check_node(structure_json)


def has_grid_type(structure_json) -> bool:
    """structure_json에 Grid 타입이 포함되어 있는지 확인"""
    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return False

    if structure_json is None:
        return False

    def check_node(node):
        if isinstance(node, dict):
            node_type = node.get('type', '')
            if node_type == 'Grid':
                return True

            children = node.get('children', [])
            for child in children:
                if check_node(child):
                    return True

        elif isinstance(node, list):
            for item in node:
                if check_node(item):
                    return True

        return False

    return check_node(structure_json)


def get_bbox(node: Dict) -> Optional[Tuple[float, float, float, float]]:
    """노드의 bounding box 반환 (x1, y1, x2, y2)"""
    pos = node.get('position', {})
    if not pos:
        return None
    x, y = pos.get('x', 0), pos.get('y', 0)
    w, h = pos.get('width', 0), pos.get('height', 0)
    return (x, y, x + w, y + h)


def is_overlapping(bbox1: Tuple, bbox2: Tuple, threshold: float = 0.1) -> bool:
    """두 bbox가 겹치는지 확인 (threshold: 작은 면적 대비 교집합 비율)"""
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


def to_absolute_coords(node: Dict, parent_x: float = 0, parent_y: float = 0) -> Dict:
    """노드와 자식들의 좌표를 절대좌표로 변환"""
    from copy import deepcopy
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


def has_frame_overlap(structure_json) -> bool:
    """structure_json에서 Frame과 겹치는 요소가 있는지 확인 (전체 트리, 절대좌표 기준)"""
    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return False

    if structure_json is None:
        return False

    # 절대좌표로 변환
    structure_abs = to_absolute_coords(structure_json)

    # 전체 트리에서 모든 Frame과 다른 요소 수집
    frames = []  # [(node, bbox), ...]
    others = []  # [(node, bbox), ...]

    def collect_nodes(node):
        if not isinstance(node, dict):
            return

        node_type = node.get('type', '')
        bbox = get_bbox(node)

        if bbox:
            if node_type == 'Frame':
                frames.append((node, bbox))
            elif node_type in ['SVG', 'Image', 'Text']:  # 요소 타입만
                others.append((node, bbox))

        # 자식 재귀 수집
        for child in node.get('children', []):
            collect_nodes(child)

    collect_nodes(structure_abs)

    # 모든 Frame과 모든 다른 요소 간 겹침 검사
    for frame, frame_bbox in frames:
        for other, other_bbox in others:
            if is_overlapping(frame_bbox, other_bbox):
                return True

    return False


# ============================================================
# 컨테이너 구성 분석
# ============================================================
def analyze_container_composition(node: Dict, results: Dict) -> None:
    """
    ZStack/Group의 자식 구성을 분석합니다.
    
    케이스:
    - zstack_mixed: ZStack 안에 요소 + VStack/HStack 혼합
    - zstack_elements_only: ZStack 안에 요소만 (컨테이너 없음)
    - zstack_containers_only: ZStack 안에 컨테이너만
    - group_mixed: Group 안에 요소 + VStack/HStack 혼합
    - group_elements_only: Group 안에 요소만
    - group_containers_only: Group 안에 컨테이너만
    """
    node_type = get_type(node)
    children = node.get('children', [])
    
    if node_type in ['ZStack', 'Group'] and children:
        # 자식 타입 분류
        has_element = False
        has_layout_container = False  # VStack/HStack
        has_any_container = False     # 모든 컨테이너
        
        child_types = []
        for child in children:
            child_type = get_type(child)
            child_types.append(child_type)
            
            if child_type in ELEMENT_TYPES:
                has_element = True
            if child_type in LAYOUT_CONTAINER_TYPES:
                has_layout_container = True
            if child_type in ALL_CONTAINER_TYPES:
                has_any_container = True
        
        prefix = 'zstack' if node_type == 'ZStack' else 'group'
        
        # 케이스 분류
        if has_element and has_layout_container:
            # 케이스 1: 요소 + VStack/HStack 혼합
            results[f'{prefix}_mixed'] += 1
            if len(results[f'{prefix}_mixed_details']) < 10:
                results[f'{prefix}_mixed_details'].append(child_types)
        elif has_element and not has_any_container:
            # 케이스 2: 요소만 (컨테이너 전혀 없음)
            results[f'{prefix}_elements_only'] += 1
            if len(results[f'{prefix}_elements_only_details']) < 10:
                results[f'{prefix}_elements_only_details'].append(child_types)
        elif not has_element and has_any_container:
            # 컨테이너만
            results[f'{prefix}_containers_only'] += 1
        else:
            # 기타 (요소 + 다른 컨테이너, 또는 빈 경우 등)
            results[f'{prefix}_other'] += 1
    
    # 자식 재귀 처리
    for child in children:
        analyze_container_composition(child, results)


def analyze_row(row: Dict) -> Tuple[Optional[Dict], str]:
    """단일 row 분석"""
    structure_json = row.get('structure_json')
    if not structure_json:
        return (None, "no_structure")

    if has_excluded_structure_role(structure_json):
        return (None, "page_role_skipped")

    if has_grid_type(structure_json):
        return (None, "grid_skipped")

    if EXCLUDE_FRAME_OVERLAP and has_frame_overlap(structure_json):
        return (None, "frame_overlap_skipped")

    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return (None, "parse_error")
    
    # 컨테이너 구성 분석
    results = {
        'zstack_mixed': 0,
        'zstack_elements_only': 0,
        'zstack_containers_only': 0,
        'zstack_other': 0,
        'zstack_mixed_details': [],
        'zstack_elements_only_details': [],
        'group_mixed': 0,
        'group_elements_only': 0,
        'group_containers_only': 0,
        'group_other': 0,
        'group_mixed_details': [],
        'group_elements_only_details': []
    }
    analyze_container_composition(structure_json, results)
    
    # 모두 0이면 스킵
    total = (results['zstack_mixed'] + results['zstack_elements_only'] + 
             results['zstack_containers_only'] + results['zstack_other'] +
             results['group_mixed'] + results['group_elements_only'] +
             results['group_containers_only'] + results['group_other'])
    
    if total == 0:
        return (None, "no_match")
    
    return ({
        "id": row["id"],
        "layout_id": row.get("layout_id"),
        "zstack": {
            "mixed": results['zstack_mixed'],
            "elements_only": results['zstack_elements_only'],
            "containers_only": results['zstack_containers_only'],
            "other": results['zstack_other']
        },
        "group": {
            "mixed": results['group_mixed'],
            "elements_only": results['group_elements_only'],
            "containers_only": results['group_containers_only'],
            "other": results['group_other']
        },
        "zstack_mixed_details": results['zstack_mixed_details'],
        "zstack_elements_only_details": results['zstack_elements_only_details'],
        "group_mixed_details": results['group_mixed_details'],
        "group_elements_only_details": results['group_elements_only_details']
    }, "found")


# ============================================================
# DB 함수
# ============================================================
def get_total_count() -> int:
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            role_placeholders = ', '.join(['%s'] * len(EXCLUDED_ROLES))
            
            cur.execute(f"""
                SELECT COUNT(*) 
                FROM design_objects 
                WHERE inference_model_type = 'agentic'
                  AND (design_object_role IS NULL OR design_object_role NOT IN ({role_placeholders}))
                  AND design_object_meta IS NOT NULL
                  AND (design_object_meta->'structure'->>'max_depth')::int >= %s
                  AND (design_object_meta->'structure'->>'max_depth')::int <= %s
            """, (*EXCLUDED_ROLES, MIN_DEPTH, MAX_DEPTH))
            return cur.fetchone()[0]
    finally:
        conn.close()


def fetch_design_objects_batch(offset: int, limit: int) -> List[Dict]:
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            role_placeholders = ', '.join(['%s'] * len(EXCLUDED_ROLES))
            
            cur.execute(f"""
                SELECT 
                    id,
                    layout_id,
                    structure_json
                FROM design_objects 
                WHERE inference_model_type = 'agentic'
                  AND (design_object_role IS NULL OR design_object_role NOT IN ({role_placeholders}))
                  AND design_object_meta IS NOT NULL
                  AND (design_object_meta->'structure'->>'max_depth')::int >= %s
                  AND (design_object_meta->'structure'->>'max_depth')::int <= %s
                ORDER BY id
                OFFSET %s LIMIT %s
            """, (*EXCLUDED_ROLES, MIN_DEPTH, MAX_DEPTH, offset, limit))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ============================================================
# 메인
# ============================================================
def main():
    print("=" * 60)
    print("ZStack/Group 내부 구성 분석")
    print("=" * 60)
    print(f"⚡ 병렬 처리: {NUM_WORKERS} workers")
    print(f"📦 배치 크기: {BATCH_SIZE:,}")
    filters = [f"depth {MIN_DEPTH}~{MAX_DEPTH}", "Page* 제외", "Grid 포함 제외"]
    if EXCLUDE_FRAME_OVERLAP:
        filters.append("Frame 겹침 제외")
    print(f"📋 필터링: {', '.join(filters)}")
    print()
    print(f"📋 요소 타입: {ELEMENT_TYPES}")
    print(f"📋 레이아웃 컨테이너: {LAYOUT_CONTAINER_TYPES}")
    
    start_time = time.time()
    
    # 전체 개수 확인
    total_count = get_total_count()
    print(f"\n📊 전체 agentic design_objects: {total_count:,}개")
    
    # 결과 저장용
    matched_items = []
    status_counts = defaultdict(int)
    all_layout_ids = set()
    
    # 케이스별 통계
    totals = {
        'zstack_mixed': 0,
        'zstack_elements_only': 0,
        'zstack_containers_only': 0,
        'zstack_other': 0,
        'group_mixed': 0,
        'group_elements_only': 0,
        'group_containers_only': 0,
        'group_other': 0
    }
    
    # 케이스별 layout_id
    layout_ids = {
        'zstack_mixed': set(),
        'zstack_elements_only': set(),
        'group_mixed': set(),
        'group_elements_only': set()
    }
    
    # 배치 처리
    processed = 0
    offset = 0
    
    with Pool(NUM_WORKERS) as pool:
        while offset < total_count:
            batch_start = time.time()
            
            batch = fetch_design_objects_batch(offset, BATCH_SIZE)
            if not batch:
                break
            
            results = pool.map(analyze_row, batch)
            
            for result, status in results:
                status_counts[status] += 1
                
                if result:
                    # 통계 합산
                    for key in ['mixed', 'elements_only', 'containers_only', 'other']:
                        totals[f'zstack_{key}'] += result['zstack'][key]
                        totals[f'group_{key}'] += result['group'][key]
                    
                    # layout_id 수집
                    lid = result.get('layout_id')
                    if lid:
                        if result['zstack']['mixed'] > 0:
                            layout_ids['zstack_mixed'].add(lid)
                        if result['zstack']['elements_only'] > 0:
                            layout_ids['zstack_elements_only'].add(lid)
                        if result['group']['mixed'] > 0:
                            layout_ids['group_mixed'].add(lid)
                        if result['group']['elements_only'] > 0:
                            layout_ids['group_elements_only'].add(lid)
                    
                    matched_items.append(result)
            
            # 전체 layout_id 수집
            for row in batch:
                if row.get('layout_id'):
                    all_layout_ids.add(row['layout_id'])
            
            processed += len(batch)
            batch_time = time.time() - batch_start
            speed = len(batch) / batch_time if batch_time > 0 else 0
            
            print(f"  ✅ {processed:,}/{total_count:,} ({processed*100/total_count:.1f}%) "
                  f"| 속도: {speed:.0f}/s "
                  f"| 매칭: {len(matched_items):,}개")
            
            offset += BATCH_SIZE
    
    elapsed = time.time() - start_time
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("📋 결과 요약")
    print("=" * 60)
    print(f"\n⏱️  총 소요 시간: {elapsed:.1f}초")
    
    print(f"\n📊 처리 통계:")
    print(f"   - DB 필터링 후: {total_count:,}개")
    print(f"   - 고유 layout_id: {len(all_layout_ids):,}개")
    print(f"   - Page* 패턴 스킵: {status_counts.get('page_role_skipped', 0):,}개")
    print(f"   - Grid 포함 스킵: {status_counts.get('grid_skipped', 0):,}개")
    print(f"   - Frame 겹침 스킵: {status_counts.get('frame_overlap_skipped', 0):,}개")
    print(f"   - 해당 없음: {status_counts.get('no_match', 0):,}개")
    print(f"   - 매칭됨: {len(matched_items):,}개")
    
    # ZStack 통계
    zstack_total = totals['zstack_mixed'] + totals['zstack_elements_only'] + totals['zstack_containers_only'] + totals['zstack_other']
    print(f"\n🔵 ZStack 내부 구성 (총 {zstack_total:,}개):")
    print(f"   ┌{'─'*55}┐")
    print(f"   │ {'케이스':<30} │ {'개수':>10} │ {'고유 layout':>10} │")
    print(f"   ├{'─'*55}┤")
    print(f"   │ {'요소 + VStack/HStack 혼합':<30} │ {totals['zstack_mixed']:>10,} │ {len(layout_ids['zstack_mixed']):>10,} │")
    print(f"   │ {'요소만 (컨테이너 없음)':<30} │ {totals['zstack_elements_only']:>10,} │ {len(layout_ids['zstack_elements_only']):>10,} │")
    print(f"   │ {'컨테이너만':<30} │ {totals['zstack_containers_only']:>10,} │ {'-':>10} │")
    print(f"   │ {'기타':<30} │ {totals['zstack_other']:>10,} │ {'-':>10} │")
    print(f"   └{'─'*55}┘")
    
    # Group 통계
    group_total = totals['group_mixed'] + totals['group_elements_only'] + totals['group_containers_only'] + totals['group_other']
    print(f"\n🟢 Group 내부 구성 (총 {group_total:,}개):")
    print(f"   ┌{'─'*55}┐")
    print(f"   │ {'케이스':<30} │ {'개수':>10} │ {'고유 layout':>10} │")
    print(f"   ├{'─'*55}┤")
    print(f"   │ {'요소 + VStack/HStack 혼합':<30} │ {totals['group_mixed']:>10,} │ {len(layout_ids['group_mixed']):>10,} │")
    print(f"   │ {'요소만 (컨테이너 없음)':<30} │ {totals['group_elements_only']:>10,} │ {len(layout_ids['group_elements_only']):>10,} │")
    print(f"   │ {'컨테이너만':<30} │ {totals['group_containers_only']:>10,} │ {'-':>10} │")
    print(f"   │ {'기타':<30} │ {totals['group_other']:>10,} │ {'-':>10} │")
    print(f"   └{'─'*55}┘")
    
    # 샘플 출력 - ZStack 혼합
    zstack_mixed_samples = [item for item in matched_items if item['zstack']['mixed'] > 0][:5]
    if zstack_mixed_samples:
        print(f"\n📝 ZStack 혼합 샘플 (처음 5개):")
        for item in zstack_mixed_samples:
            details = item.get('zstack_mixed_details', [])[:2]
            print(f"  - ID: {item['id']}")
            for d in details:
                print(f"      자식: {d}")
    
    # 샘플 출력 - ZStack 요소만
    zstack_elem_samples = [item for item in matched_items if item['zstack']['elements_only'] > 0][:5]
    if zstack_elem_samples:
        print(f"\n📝 ZStack 요소만 샘플 (처음 5개):")
        for item in zstack_elem_samples:
            details = item.get('zstack_elements_only_details', [])[:2]
            print(f"  - ID: {item['id']}")
            for d in details:
                print(f"      자식: {d}")
    
    # JSON 저장
    output_path = "data/container_composition.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "total_analyzed": total_count,
                "unique_layout_ids": len(all_layout_ids),
                "matched_items": len(matched_items),
                "zstack": {
                    "mixed": totals['zstack_mixed'],
                    "mixed_layouts": len(layout_ids['zstack_mixed']),
                    "elements_only": totals['zstack_elements_only'],
                    "elements_only_layouts": len(layout_ids['zstack_elements_only']),
                    "containers_only": totals['zstack_containers_only'],
                    "other": totals['zstack_other']
                },
                "group": {
                    "mixed": totals['group_mixed'],
                    "mixed_layouts": len(layout_ids['group_mixed']),
                    "elements_only": totals['group_elements_only'],
                    "elements_only_layouts": len(layout_ids['group_elements_only']),
                    "containers_only": totals['group_containers_only'],
                    "other": totals['group_other']
                }
            },
            "items": matched_items[:1000]  # 처음 1000개만 저장
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n📁 결과 저장: {output_path}")


if __name__ == "__main__":
    main()
