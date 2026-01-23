#!/usr/bin/env python3
"""
Frame과 겹치는 요소 개수 파악

find_fix_candidates.py의 필터링 로직을 사용하여
Frame 타입이 다른 요소와 겹치는 경우를 분석합니다.
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
# 설정 (find_fix_candidates.py와 동일)
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


# ============================================================
# 헬퍼 함수들
# ============================================================
def get_role(node: Dict) -> str:
    role = node.get('role', '')
    return role.split('.')[-1] if '.' in role else role


def get_type(node: Dict) -> str:
    return node.get('type', '')


def get_bbox(node: Dict) -> Optional[Tuple[float, float, float, float]]:
    pos = node.get('position', {})
    if not pos:
        return None
    x = pos.get('x', 0)
    y = pos.get('y', 0)
    w = pos.get('width', 0)
    h = pos.get('height', 0)
    return (x, y, x + w, y + h)


def is_overlapping(bbox1: Tuple, bbox2: Tuple) -> bool:
    """두 박스가 겹치는지 확인 (단순 겹침)"""
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2
    
    inter_x_min = max(x1_min, x2_min)
    inter_y_min = max(y1_min, y2_min)
    inter_x_max = min(x1_max, x2_max)
    inter_y_max = min(y1_max, y2_max)
    
    if inter_x_max <= inter_x_min or inter_y_max <= inter_y_min:
        return False
    
    return True


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


# ============================================================
# Frame 겹침 분석
# ============================================================
CONTAINER_TYPES = ['Group', 'HStack', 'VStack', 'ZStack', 'Grid']

def find_frame_overlaps(node: Dict, results: Dict) -> None:
    """
    노드에서 Frame과 겹치는 요소를 찾습니다.
    
    카운트 케이스:
    - frame_total: Frame과 겹치는 모든 경우 (Frame, Image, Background 제외)
    - frame_marker: Frame + Marker
    - frame_decoration: Frame + Decoration
    - frame_container: Frame + Container (Group, HStack, VStack, ZStack, Grid)
    - frame_title: Frame + Title
    - frame_other: 그 외
    """
    children = node.get('children', [])
    
    if children:
        for i in range(len(children)):
            bbox_i = get_bbox(children[i])
            if not bbox_i:
                continue
            
            type_i = get_type(children[i])
            role_i = get_role(children[i])
            
            for j in range(i + 1, len(children)):
                bbox_j = get_bbox(children[j])
                if not bbox_j:
                    continue
                
                type_j = get_type(children[j])
                role_j = get_role(children[j])
                
                # Background 제외
                if role_i == 'Background' or role_j == 'Background':
                    continue
                
                # Frame이 포함되어 있는지 확인
                if type_i == 'Frame':
                    frame_type, frame_role = type_i, role_i
                    other_type, other_role = type_j, role_j
                elif type_j == 'Frame':
                    frame_type, frame_role = type_j, role_j
                    other_type, other_role = type_i, role_i
                else:
                    continue  # Frame이 없으면 스킵
                
                # Frame, Image도 제외 (Frame끼리, Frame+Image)
                if other_type in ['Frame', 'Image']:
                    continue
                
                # 겹침 확인
                if not is_overlapping(bbox_i, bbox_j):
                    continue
                
                # 총 카운트
                results['frame_total'] += 1
                
                # 케이스별 카운트
                if other_role == 'Marker':
                    results['frame_marker'] += 1
                elif other_role == 'Decoration':
                    results['frame_decoration'] += 1
                elif other_type in CONTAINER_TYPES:
                    results['frame_container'] += 1
                elif other_role == 'Title':
                    results['frame_title'] += 1
                else:
                    results['frame_other'] += 1
                    # 기타 케이스 상세 (처음 10개만)
                    if len(results['frame_other_details']) < 10:
                        results['frame_other_details'].append(f"{other_type}({other_role})")
                
                # 상세 정보 (처음 5개만)
                if len(results['frame_overlap_details']) < 5:
                    results['frame_overlap_details'].append({
                        'elem1': f"{type_i}({role_i})",
                        'elem2': f"{type_j}({role_j})"
                    })
        
        # 자식 재귀 처리
        for child in children:
            find_frame_overlaps(child, results)


def analyze_row(row: Dict) -> Tuple[Optional[Dict], str]:
    """단일 row 분석"""
    structure_json = row.get('structure_json')
    if not structure_json:
        return (None, "no_structure")
    
    if has_excluded_structure_role(structure_json):
        return (None, "page_role_skipped")
    
    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return (None, "parse_error")
    
    # Frame 겹침 분석
    results = {
        'frame_total': 0,
        'frame_marker': 0,
        'frame_decoration': 0,
        'frame_container': 0,
        'frame_title': 0,
        'frame_other': 0,
        'frame_other_details': [],
        'frame_overlap_details': []
    }
    find_frame_overlaps(structure_json, results)
    
    if results['frame_total'] == 0:
        return (None, "no_frame_overlap")
    
    return ({
        "id": row["id"],
        "layout_id": row.get("layout_id"),
        "counts": {
            "total": results['frame_total'],
            "marker": results['frame_marker'],
            "decoration": results['frame_decoration'],
            "container": results['frame_container'],
            "title": results['frame_title'],
            "other": results['frame_other']
        },
        "other_details": results['frame_other_details'],
        "overlap_details": results['frame_overlap_details']
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
    print("Frame 겹침 분석")
    print("=" * 60)
    print(f"⚡ 병렬 처리: {NUM_WORKERS} workers")
    print(f"📦 배치 크기: {BATCH_SIZE:,}")
    print(f"📋 필터링: depth {MIN_DEPTH}~{MAX_DEPTH}, Page* 제외")
    print(f"📋 제외: Frame+Frame, Frame+Image, Frame+Background")
    
    start_time = time.time()
    
    # 전체 개수 확인
    total_count = get_total_count()
    print(f"\n📊 전체 agentic design_objects: {total_count:,}개")
    
    # 결과 저장용
    frame_overlap_items = []
    status_counts = defaultdict(int)
    all_layout_ids = set()  # 전체 고유 layout_id
    overlap_layout_ids = set()  # Frame 겹침이 있는 고유 layout_id
    
    # 케이스별 총 카운트
    case_totals = {
        'total': 0,
        'marker': 0,
        'decoration': 0,
        'container': 0,
        'title': 0,
        'other': 0
    }
    other_details_all = defaultdict(int)  # 기타 케이스 상세
    
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
                    # 케이스별 합산
                    for key in case_totals:
                        case_totals[key] += result['counts'].get(key, 0)
                    
                    # 기타 케이스 상세 수집
                    for detail in result.get('other_details', []):
                        other_details_all[detail] += 1
                    
                    # layout_id 수집
                    if result.get('layout_id'):
                        overlap_layout_ids.add(result['layout_id'])
                    
                    frame_overlap_items.append(result)
            
            # 전체 layout_id 수집
            for row in batch:
                if row.get('layout_id'):
                    all_layout_ids.add(row['layout_id'])
            
            processed += len(batch)
            batch_time = time.time() - batch_start
            speed = len(batch) / batch_time if batch_time > 0 else 0
            
            print(f"  ✅ {processed:,}/{total_count:,} ({processed*100/total_count:.1f}%) "
                  f"| 속도: {speed:.0f}/s "
                  f"| 항목: {len(frame_overlap_items):,}개 "
                  f"| 총: {case_totals['total']:,}쌍")
            
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
    print(f"   - structure_json 없음: {status_counts.get('no_structure', 0):,}개")
    print(f"   - JSON 파싱 실패: {status_counts.get('parse_error', 0):,}개")
    print(f"   - Frame 겹침 없음: {status_counts.get('no_frame_overlap', 0):,}개")
    print(f"   - Frame 겹침 있음: {len(frame_overlap_items):,}개 (고유 layout: {len(overlap_layout_ids):,}개)")
    
    print(f"\n🔴 Frame 겹침 케이스별 통계:")
    print(f"   ┌{'─'*40}┐")
    print(f"   │ {'케이스':<20} │ {'개수':>10} │ {'비율':>6} │")
    print(f"   ├{'─'*40}┤")
    print(f"   │ {'Frame + Marker':<20} │ {case_totals['marker']:>10,} │ {case_totals['marker']*100/case_totals['total'] if case_totals['total'] > 0 else 0:>5.1f}% │")
    print(f"   │ {'Frame + Decoration':<20} │ {case_totals['decoration']:>10,} │ {case_totals['decoration']*100/case_totals['total'] if case_totals['total'] > 0 else 0:>5.1f}% │")
    print(f"   │ {'Frame + Container':<20} │ {case_totals['container']:>10,} │ {case_totals['container']*100/case_totals['total'] if case_totals['total'] > 0 else 0:>5.1f}% │")
    print(f"   │ {'Frame + Title':<20} │ {case_totals['title']:>10,} │ {case_totals['title']*100/case_totals['total'] if case_totals['total'] > 0 else 0:>5.1f}% │")
    print(f"   │ {'Frame + Other':<20} │ {case_totals['other']:>10,} │ {case_totals['other']*100/case_totals['total'] if case_totals['total'] > 0 else 0:>5.1f}% │")
    print(f"   ├{'─'*40}┤")
    print(f"   │ {'총계':<20} │ {case_totals['total']:>10,} │ {'100.0'}% │")
    print(f"   └{'─'*40}┘")
    
    # 기타 케이스 상세
    if other_details_all:
        print(f"\n📋 Frame + Other 상세:")
        for detail, count in sorted(other_details_all.items(), key=lambda x: -x[1])[:10]:
            print(f"   - {detail}: {count:,}건")
    
    # 샘플 출력
    if frame_overlap_items:
        print(f"\n📝 샘플 (처음 5개):")
        for item in frame_overlap_items[:5]:
            print(f"  - ID: {item['id']}, 총 {item['counts']['total']}쌍 "
                  f"(M:{item['counts']['marker']}, D:{item['counts']['decoration']}, "
                  f"C:{item['counts']['container']}, T:{item['counts']['title']})")
    
    # JSON 저장
    output_path = "data/frame_overlaps.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "total_analyzed": total_count,
                "unique_layout_ids": len(all_layout_ids),
                "page_skipped": status_counts.get('page_role_skipped', 0),
                "frame_overlap_items": len(frame_overlap_items),
                "frame_overlap_layout_ids": len(overlap_layout_ids),
                "case_totals": case_totals,
                "other_details": dict(other_details_all)
            },
            "items": frame_overlap_items
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n📁 결과 저장: {output_path}")


if __name__ == "__main__":
    main()
