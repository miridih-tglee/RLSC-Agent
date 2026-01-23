#!/usr/bin/env python3
"""
ZStack/Group의 자식 개수 분석

find_fix_candidates.py의 필터링 로직을 사용하여
ZStack 또는 Group에 자식이 3개 이상인 경우를 분석합니다.
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

# 분석 대상
MIN_CHILDREN = 3  # 자식 최소 개수


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


# ============================================================
# 컨테이너 자식 분석
# ============================================================
def find_containers_with_many_children(node: Dict, results: Dict) -> None:
    """
    노드에서 ZStack/Group 중 자식이 3개 이상인 경우를 찾습니다.
    
    results에 추가:
    - zstack_count: ZStack 중 자식 3개 이상인 개수
    - group_count: Group 중 자식 3개 이상인 개수
    - zstack_details: ZStack 상세 (자식 수)
    - group_details: Group 상세 (자식 수)
    """
    node_type = get_type(node)
    children = node.get('children', [])
    num_children = len(children)
    
    # ZStack 검사
    if node_type == 'ZStack' and num_children >= MIN_CHILDREN:
        results['zstack_count'] += 1
        results['zstack_children_sum'] += num_children
        if len(results['zstack_details']) < 10:
            results['zstack_details'].append(num_children)
    
    # Group 검사
    if node_type == 'Group' and num_children >= MIN_CHILDREN:
        results['group_count'] += 1
        results['group_children_sum'] += num_children
        if len(results['group_details']) < 10:
            results['group_details'].append(num_children)
    
    # 자식 재귀 처리
    for child in children:
        find_containers_with_many_children(child, results)


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
    
    # 컨테이너 분석
    results = {
        'zstack_count': 0,
        'group_count': 0,
        'zstack_children_sum': 0,
        'group_children_sum': 0,
        'zstack_details': [],
        'group_details': []
    }
    find_containers_with_many_children(structure_json, results)
    
    # 둘 다 없으면 스킵
    if results['zstack_count'] == 0 and results['group_count'] == 0:
        return (None, "no_match")
    
    return ({
        "id": row["id"],
        "layout_id": row.get("layout_id"),
        "zstack_count": results['zstack_count'],
        "group_count": results['group_count'],
        "zstack_children_sum": results['zstack_children_sum'],
        "group_children_sum": results['group_children_sum'],
        "zstack_details": results['zstack_details'],
        "group_details": results['group_details']
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
    print(f"ZStack/Group 자식 개수 분석 (>= {MIN_CHILDREN}개)")
    print("=" * 60)
    print(f"⚡ 병렬 처리: {NUM_WORKERS} workers")
    print(f"📦 배치 크기: {BATCH_SIZE:,}")
    print(f"📋 필터링: depth {MIN_DEPTH}~{MAX_DEPTH}, Page* 제외")
    
    start_time = time.time()
    
    # 전체 개수 확인
    total_count = get_total_count()
    print(f"\n📊 전체 agentic design_objects: {total_count:,}개")
    
    # 결과 저장용
    matched_items = []
    status_counts = defaultdict(int)
    all_layout_ids = set()
    matched_layout_ids = set()
    
    # 전체 통계
    total_stats = {
        'zstack_items': 0,      # ZStack이 있는 item 수
        'group_items': 0,       # Group이 있는 item 수
        'zstack_total': 0,      # ZStack 총 개수
        'group_total': 0,       # Group 총 개수
        'zstack_children': 0,   # ZStack 자식 총합
        'group_children': 0     # Group 자식 총합
    }
    
    # 타입별 고유 layout_id
    zstack_layout_ids = set()
    group_layout_ids = set()
    
    # 자식 수 분포
    zstack_dist = defaultdict(int)  # {자식수: 개수}
    group_dist = defaultdict(int)
    
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
                    if result['zstack_count'] > 0:
                        total_stats['zstack_items'] += 1
                        total_stats['zstack_total'] += result['zstack_count']
                        total_stats['zstack_children'] += result['zstack_children_sum']
                        for n in result['zstack_details']:
                            zstack_dist[n] += 1
                        # ZStack layout_id 수집
                        if result.get('layout_id'):
                            zstack_layout_ids.add(result['layout_id'])
                    
                    if result['group_count'] > 0:
                        total_stats['group_items'] += 1
                        total_stats['group_total'] += result['group_count']
                        total_stats['group_children'] += result['group_children_sum']
                        for n in result['group_details']:
                            group_dist[n] += 1
                        # Group layout_id 수집
                        if result.get('layout_id'):
                            group_layout_ids.add(result['layout_id'])
                    
                    # layout_id 수집
                    if result.get('layout_id'):
                        matched_layout_ids.add(result['layout_id'])
                    
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
    print(f"   - structure_json 없음: {status_counts.get('no_structure', 0):,}개")
    print(f"   - JSON 파싱 실패: {status_counts.get('parse_error', 0):,}개")
    print(f"   - 해당 없음: {status_counts.get('no_match', 0):,}개")
    print(f"   - 매칭됨: {len(matched_items):,}개 (고유 layout: {len(matched_layout_ids):,}개)")
    
    print(f"\n🔵 ZStack (자식 >= {MIN_CHILDREN}) 통계:")
    print(f"   - 해당 item 수: {total_stats['zstack_items']:,}개")
    print(f"   - 고유 layout_id: {len(zstack_layout_ids):,}개")
    print(f"   - ZStack 총 개수: {total_stats['zstack_total']:,}개")
    print(f"   - 자식 총 합계: {total_stats['zstack_children']:,}개")
    if total_stats['zstack_total'] > 0:
        print(f"   - 평균 자식 수: {total_stats['zstack_children']/total_stats['zstack_total']:.1f}개")
    
    print(f"\n🟢 Group (자식 >= {MIN_CHILDREN}) 통계:")
    print(f"   - 해당 item 수: {total_stats['group_items']:,}개")
    print(f"   - 고유 layout_id: {len(group_layout_ids):,}개")
    print(f"   - Group 총 개수: {total_stats['group_total']:,}개")
    print(f"   - 자식 총 합계: {total_stats['group_children']:,}개")
    if total_stats['group_total'] > 0:
        print(f"   - 평균 자식 수: {total_stats['group_children']/total_stats['group_total']:.1f}개")
    
    # 자식 수 분포 출력
    if zstack_dist:
        print(f"\n📊 ZStack 자식 수 분포:")
        for n in sorted(zstack_dist.keys())[:10]:
            print(f"   - {n}개: {zstack_dist[n]:,}건")
        if len(zstack_dist) > 10:
            print(f"   - ... (총 {len(zstack_dist)}종류)")
    
    if group_dist:
        print(f"\n📊 Group 자식 수 분포:")
        for n in sorted(group_dist.keys())[:10]:
            print(f"   - {n}개: {group_dist[n]:,}건")
        if len(group_dist) > 10:
            print(f"   - ... (총 {len(group_dist)}종류)")
    
    # 샘플 출력
    if matched_items:
        print(f"\n📝 샘플 (처음 5개):")
        for item in matched_items[:5]:
            print(f"  - ID: {item['id']}, ZStack: {item['zstack_count']}개, Group: {item['group_count']}개")
    
    # JSON 저장
    output_path = "data/container_children.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "min_children": MIN_CHILDREN,
                "total_analyzed": total_count,
                "unique_layout_ids": len(all_layout_ids),
                "page_skipped": status_counts.get('page_role_skipped', 0),
                "matched_items": len(matched_items),
                "matched_layout_ids": len(matched_layout_ids),
                "zstack_stats": {
                    "items": total_stats['zstack_items'],
                    "unique_layout_ids": len(zstack_layout_ids),
                    "total": total_stats['zstack_total'],
                    "children_sum": total_stats['zstack_children'],
                    "distribution": dict(zstack_dist)
                },
                "group_stats": {
                    "items": total_stats['group_items'],
                    "unique_layout_ids": len(group_layout_ids),
                    "total": total_stats['group_total'],
                    "children_sum": total_stats['group_children'],
                    "distribution": dict(group_dist)
                }
            },
            "items": matched_items
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n📁 결과 저장: {output_path}")


if __name__ == "__main__":
    main()
