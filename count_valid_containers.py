#!/usr/bin/env python3
"""
유효한 ZStack/Group 분석

유효한 ZStack/Group 조건 (반드시 Background 필요 + 아래 중 하나):
1. VStack 1개 + Background(SVG/Image)
2. HStack 1개 + Background(SVG/Image)
3. Element 1개 + Background(SVG/Image)

Element roles: Title, Subtitle, Highlight, Description, Separator, Marker, Decoration

디자인 오브젝트 분류:
- valid: 모든 ZStack/Group이 유효
- invalid: 하나라도 유효하지 않은 ZStack/Group이 있음
- no_container: ZStack/Group이 없음
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


# ============================================================
# 헬퍼 함수들
# ============================================================
def get_type(node: Dict) -> str:
    return node.get('type', '')


def get_role(node: Dict) -> str:
    role = node.get('role', '')
    return role.split('.')[-1] if '.' in role else role


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
# 유효성 검사
# ============================================================
def is_valid_container(node: Dict) -> Tuple[bool, str]:
    """
    ZStack/Group이 유효한지 검사합니다.
    
    유효 조건 (반드시 Background 필요 + 아래 중 하나):
    1. VStack 1개 + Background(SVG/Image)
    2. HStack 1개 + Background(SVG/Image)
    3. Element 1개 + Background(SVG/Image)
    
    Element roles: Title, Subtitle, Highlight, Description, Separator, Marker, Decoration
    
    반환: (is_valid, reason)
    """
    children = node.get('children', [])
    
    if not children:
        return (False, "invalid")
    
    # Element roles (허용되는 요소들)
    ELEMENT_ROLES = ['Title', 'Subtitle', 'Highlight', 'Description', 
                     'Separator', 'Marker', 'Decoration']
    
    # 자식 분류
    vstack_count = 0              # VStack 개수
    hstack_count = 0              # HStack 개수
    background_count = 0          # Background 개수
    element_count = 0             # Element 개수
    
    for child in children:
        child_type = get_type(child)
        child_role = get_role(child)
        
        if child_type == 'VStack':
            vstack_count += 1
        elif child_type == 'HStack':
            hstack_count += 1
        elif child_type in ['SVG', 'Image'] and child_role == 'Background':
            background_count += 1
        elif child_role in ELEMENT_ROLES:
            element_count += 1
    
    # 유효 조건: Background 1개 + (VStack 1개 or HStack 1개 or Element 1개)
    if background_count == 1:
        # 조건1: VStack 1개 + Background
        if vstack_count == 1 and hstack_count == 0 and element_count == 0:
            return (True, "valid_vstack_bg")
        
        # 조건2: HStack 1개 + Background
        if hstack_count == 1 and vstack_count == 0 and element_count == 0:
            return (True, "valid_hstack_bg")
        
        # 조건3: Element 1개 + Background
        if element_count == 1 and vstack_count == 0 and hstack_count == 0:
            return (True, "valid_element_bg")
    
    # 나머지는 전부 invalid
    return (False, "invalid")


def analyze_containers(node: Dict, results: Dict) -> None:
    """
    트리를 순회하며 모든 ZStack/Group의 유효성을 검사합니다.
    
    results에 추가:
    - valid_count, invalid_count: 전체
    - zstack_valid, zstack_invalid: ZStack만
    - group_valid, group_invalid: Group만
    - valid_compositions, invalid_compositions: 조합들
    """
    node_type = get_type(node)
    children = node.get('children', [])
    
    # ZStack/Group이면 유효성 검사
    if node_type in ['ZStack', 'Group']:
        is_valid, reason = is_valid_container(node)
        
        # 자식 구성 기록 (타입만, 중복 제거하고 정렬)
        child_types = sorted(set(get_type(c) for c in children))
        child_roles = sorted(set(get_role(c) for c in children if get_role(c)))
        composition = f"{node_type}:[types={child_types}, roles={child_roles}]"
        
        if is_valid:
            results['valid_count'] += 1
            results['valid_compositions'].append(composition)
            # 타입별 카운트
            if node_type == 'ZStack':
                results['zstack_valid'] += 1
            else:
                results['group_valid'] += 1
        else:
            results['invalid_count'] += 1
            results['invalid_reasons'].append(reason)
            results['invalid_compositions'].append(composition)
            # 타입별 카운트
            if node_type == 'ZStack':
                results['zstack_invalid'] += 1
            else:
                results['group_invalid'] += 1
            
            # 상세 정보 (처음 5개만)
            if len(results['invalid_details']) < 5:
                child_info = [f"{get_type(c)}({get_role(c)})" for c in children[:5]]
                results['invalid_details'].append({
                    'container_type': node_type,
                    'reason': reason,
                    'children': child_info
                })
    
    # 자식 재귀 처리
    for child in children:
        analyze_containers(child, results)


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
    
    # 컨테이너 유효성 분석
    results = {
        'valid_count': 0,
        'invalid_count': 0,
        'zstack_valid': 0,
        'zstack_invalid': 0,
        'group_valid': 0,
        'group_invalid': 0,
        'invalid_reasons': [],
        'invalid_details': [],
        'valid_compositions': [],
        'invalid_compositions': []
    }
    analyze_containers(structure_json, results)
    
    # ZStack/Group이 하나도 없으면
    total_containers = results['valid_count'] + results['invalid_count']
    if total_containers == 0:
        return (None, "no_container")
    
    # 분류: 하나라도 invalid가 있으면 invalid
    if results['invalid_count'] > 0:
        classification = "invalid"
    else:
        classification = "valid"
    
    # 컨테이너 타입 분류 (zstack_only, group_only, both)
    has_zstack = (results['zstack_valid'] + results['zstack_invalid']) > 0
    has_group = (results['group_valid'] + results['group_invalid']) > 0
    
    if has_zstack and has_group:
        container_type = "both"
    elif has_zstack:
        container_type = "zstack_only"
    else:
        container_type = "group_only"
    
    return ({
        "id": row["id"],
        "layout_id": row.get("layout_id"),
        "classification": classification,
        "container_type": container_type,
        "valid_count": results['valid_count'],
        "invalid_count": results['invalid_count'],
        "zstack_valid": results['zstack_valid'],
        "zstack_invalid": results['zstack_invalid'],
        "group_valid": results['group_valid'],
        "group_invalid": results['group_invalid'],
        "invalid_reasons": results['invalid_reasons'][:5],
        "invalid_details": results['invalid_details'],
        "valid_compositions": results['valid_compositions'],
        "invalid_compositions": results['invalid_compositions']
    }, classification)


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
    print("유효한 ZStack/Group 분석")
    print("=" * 60)
    print(f"⚡ 병렬 처리: {NUM_WORKERS} workers")
    print(f"📦 배치 크기: {BATCH_SIZE:,}")
    print(f"📋 필터링: depth {MIN_DEPTH}~{MAX_DEPTH}, Page* 제외")
    print()
    print("📋 유효 조건: ZStack/Group의 자식이 (Background 필수 + 아래 중 하나)")
    print("   1. VStack 1개 + Background(SVG/Image)")
    print("   2. HStack 1개 + Background(SVG/Image)")
    print("   3. Element 1개 + Background(SVG/Image)")
    print("      (Element: Title/Subtitle/Highlight/Description/Separator/Marker/Decoration)")
    print()
    print("📋 분류 기준:")
    print("   - valid: 모든 ZStack/Group이 유효")
    print("   - invalid: 하나라도 유효하지 않은 ZStack/Group이 있음")
    
    start_time = time.time()
    
    # 전체 개수 확인
    total_count = get_total_count()
    print(f"\n📊 전체 agentic design_objects: {total_count:,}개")
    
    # 결과 저장용
    valid_items = []
    invalid_items = []
    status_counts = defaultdict(int)
    all_layout_ids = set()
    
    # 분류별 layout_id
    valid_layout_ids = set()
    invalid_layout_ids = set()
    
    # 분류별 design_object ID
    valid_ids = []
    invalid_ids = []
    
    # invalid 이유 집계
    invalid_reason_counts = defaultdict(int)
    
    # 자식 구성 조합 집계 (카운트 + 고유 ID 목록)
    valid_composition_counts = defaultdict(int)
    invalid_composition_counts = defaultdict(int)
    valid_composition_ids = defaultdict(set)  # 조합별 고유 design_object ID
    invalid_composition_ids = defaultdict(set)
    valid_composition_layouts = defaultdict(set)  # 조합별 고유 layout ID
    invalid_composition_layouts = defaultdict(set)
    
    # 컨테이너 타입별 통계 (zstack_only, group_only, both)
    container_type_stats = {
        'zstack_only': {'valid': 0, 'invalid': 0, 'valid_ids': [], 'invalid_ids': []},
        'group_only': {'valid': 0, 'invalid': 0, 'valid_ids': [], 'invalid_ids': []},
        'both': {'valid': 0, 'invalid': 0, 'valid_ids': [], 'invalid_ids': []}
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
                    lid = result.get('layout_id')
                    did = result['id']
                    ctype = result.get('container_type', 'both')
                    
                    if result['classification'] == 'valid':
                        valid_items.append(result)
                        valid_ids.append(did)
                        if lid:
                            valid_layout_ids.add(lid)
                        
                        # 컨테이너 타입별 통계
                        container_type_stats[ctype]['valid'] += 1
                        container_type_stats[ctype]['valid_ids'].append(did)
                        
                        # valid 조합 집계
                        for comp in result.get('valid_compositions', []):
                            valid_composition_counts[comp] += 1
                            valid_composition_ids[comp].add(did)  # 고유 ID만
                            if lid:
                                valid_composition_layouts[comp].add(lid)
                    else:  # invalid
                        invalid_items.append(result)
                        invalid_ids.append(did)
                        if lid:
                            invalid_layout_ids.add(lid)
                        
                        # 컨테이너 타입별 통계
                        container_type_stats[ctype]['invalid'] += 1
                        container_type_stats[ctype]['invalid_ids'].append(did)
                        
                        # invalid 이유 집계
                        for reason in result.get('invalid_reasons', []):
                            # 간단히 첫 번째 이유만 추출
                            main_reason = reason.split('|')[0]
                            invalid_reason_counts[main_reason] += 1
                        
                        # invalid 조합 집계
                        for comp in result.get('invalid_compositions', []):
                            invalid_composition_counts[comp] += 1
                            invalid_composition_ids[comp].add(did)  # 고유 ID만
                            if lid:
                                invalid_composition_layouts[comp].add(lid)
            
            # 전체 layout_id 수집
            for row in batch:
                if row.get('layout_id'):
                    all_layout_ids.add(row['layout_id'])
            
            processed += len(batch)
            batch_time = time.time() - batch_start
            speed = len(batch) / batch_time if batch_time > 0 else 0
            
            print(f"  ✅ {processed:,}/{total_count:,} ({processed*100/total_count:.1f}%) "
                  f"| 속도: {speed:.0f}/s "
                  f"| valid: {len(valid_items):,} | invalid: {len(invalid_items):,}")
            
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
    print(f"   - ZStack/Group 없음: {status_counts.get('no_container', 0):,}개")
    
    print(f"\n🔵 전체 분류 결과:")
    total_with_container = len(valid_items) + len(invalid_items)
    print(f"   ┌{'─'*50}┐")
    print(f"   │ {'분류':<20} │ {'item 수':>12} │ {'고유 layout':>12} │")
    print(f"   ├{'─'*50}┤")
    print(f"   │ {'✅ valid (모두 유효)':<20} │ {len(valid_items):>12,} │ {len(valid_layout_ids):>12,} │")
    print(f"   │ {'❌ invalid (1개라도 불량)':<20} │ {len(invalid_items):>12,} │ {len(invalid_layout_ids):>12,} │")
    print(f"   ├{'─'*50}┤")
    print(f"   │ {'합계':<20} │ {total_with_container:>12,} │ {'-':>12} │")
    print(f"   └{'─'*50}┘")
    
    # invalid 비율
    if total_with_container > 0:
        invalid_ratio = len(invalid_items) * 100 / total_with_container
        print(f"\n   ⚠️  invalid 비율: {invalid_ratio:.1f}%")
    
    # 컨테이너 타입별 통계
    print(f"\n🔷 컨테이너 타입별 분류:")
    print(f"   ┌{'─'*70}┐")
    print(f"   │ {'타입':<15} │ {'valid':>10} │ {'invalid':>10} │ {'합계':>10} │ {'invalid%':>10} │")
    print(f"   ├{'─'*70}┤")
    for ctype, stats in container_type_stats.items():
        v, i = stats['valid'], stats['invalid']
        total = v + i
        ratio = (i * 100 / total) if total > 0 else 0
        label = {'zstack_only': 'ZStack만', 'group_only': 'Group만', 'both': 'ZStack+Group'}[ctype]
        print(f"   │ {label:<15} │ {v:>10,} │ {i:>10,} │ {total:>10,} │ {ratio:>9.1f}% │")
    print(f"   ├{'─'*70}┤")
    total_v = sum(s['valid'] for s in container_type_stats.values())
    total_i = sum(s['invalid'] for s in container_type_stats.values())
    total_all = total_v + total_i
    total_ratio = (total_i * 100 / total_all) if total_all > 0 else 0
    print(f"   │ {'합계':<15} │ {total_v:>10,} │ {total_i:>10,} │ {total_all:>10,} │ {total_ratio:>9.1f}% │")
    print(f"   └{'─'*70}┘")
    
    # invalid 이유 통계
    if invalid_reason_counts:
        print(f"\n📊 invalid 이유 (상위 10개):")
        for reason, count in sorted(invalid_reason_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"   - {reason}: {count:,}건")
    
    # valid 조합 통계 (고유 DO 수 기준 정렬)
    valid_sorted = sorted(
        valid_composition_ids.items(), 
        key=lambda x: -len(x[1])  # 고유 DO 수 기준 정렬
    )
    if valid_sorted:
        print(f"\n✅ valid 조합 (총 {len(valid_sorted)}종류, 상위 20개):")
        print(f"   {'DO수':>8} | {'Layout':>8} | 조합")
        print(f"   {'-'*8}-+-{'-'*8}-+------------------------------------------")
        for comp, ids in valid_sorted[:20]:
            do_cnt = len(ids)
            layout_cnt = len(valid_composition_layouts.get(comp, set()))
            print(f"   {do_cnt:>8,} | {layout_cnt:>8,} | {comp}")
    
    # invalid 조합 통계 (고유 DO 수 기준 정렬)
    invalid_sorted = sorted(
        invalid_composition_ids.items(), 
        key=lambda x: -len(x[1])  # 고유 DO 수 기준 정렬
    )
    if invalid_sorted:
        print(f"\n❌ invalid 조합 (총 {len(invalid_sorted)}종류, 상위 20개):")
        print(f"   {'DO수':>8} | {'Layout':>8} | 조합")
        print(f"   {'-'*8}-+-{'-'*8}-+------------------------------------------")
        for comp, ids in invalid_sorted[:20]:
            do_cnt = len(ids)
            layout_cnt = len(invalid_composition_layouts.get(comp, set()))
            print(f"   {do_cnt:>8,} | {layout_cnt:>8,} | {comp}")
    
    # 샘플 출력 - invalid
    if invalid_items:
        print(f"\n📝 invalid 샘플 (처음 5개):")
        for item in invalid_items[:5]:
            print(f"  - ID: {item['id']}, valid: {item['valid_count']}, invalid: {item['invalid_count']}")
            for detail in item.get('invalid_details', [])[:2]:
                print(f"      {detail['container_type']}: {detail['children']}")
                print(f"      이유: {detail['reason']}")
    
    # JSON 저장 - 메인 결과
    output_path = "data/valid_containers.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "total_analyzed": total_count,
                "unique_layout_ids": len(all_layout_ids),
                "no_container": status_counts.get('no_container', 0),
                "valid": {
                    "count": len(valid_items),
                    "layout_ids": len(valid_layout_ids)
                },
                "invalid": {
                    "count": len(invalid_items),
                    "layout_ids": len(invalid_layout_ids),
                    "reason_counts": dict(invalid_reason_counts)
                },
                "valid_compositions": dict(sorted(valid_composition_counts.items(), key=lambda x: -x[1])),
                "invalid_compositions": dict(sorted(invalid_composition_counts.items(), key=lambda x: -x[1]))
            },
            "valid_items": valid_items[:500],
            "invalid_items": invalid_items[:500]
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n📁 결과 저장: {output_path}")
    
    # ID 목록 저장 - valid
    valid_ids_path = "data/valid_container_ids.json"
    with open(valid_ids_path, 'w', encoding='utf-8') as f:
        json.dump({
            "count": len(valid_ids),
            "ids": valid_ids
        }, f, ensure_ascii=False, indent=2)
    print(f"📁 valid ID 목록: {valid_ids_path} ({len(valid_ids):,}개)")
    
    # ID 목록 저장 - invalid
    invalid_ids_path = "data/invalid_container_ids.json"
    with open(invalid_ids_path, 'w', encoding='utf-8') as f:
        json.dump({
            "count": len(invalid_ids),
            "ids": invalid_ids
        }, f, ensure_ascii=False, indent=2)
    print(f"📁 invalid ID 목록: {invalid_ids_path} ({len(invalid_ids):,}개)")
    
    # 조합별 ID 목록 저장 - valid (고유 DO 수 기준 정렬)
    valid_comp_ids_path = "data/valid_composition_ids.json"
    valid_comp_data = {}
    for comp, ids in sorted(valid_composition_ids.items(), key=lambda x: -len(x[1])):
        layouts = valid_composition_layouts.get(comp, set())
        valid_comp_data[comp] = {
            "design_object_count": len(ids),
            "layout_count": len(layouts),
            "ids": list(ids),
            "layout_ids": list(layouts)
        }
    with open(valid_comp_ids_path, 'w', encoding='utf-8') as f:
        json.dump(valid_comp_data, f, ensure_ascii=False, indent=2)
    print(f"📁 valid 조합별 ID: {valid_comp_ids_path} ({len(valid_comp_data)}종류)")
    
    # 조합별 ID 목록 저장 - invalid (고유 DO 수 기준 정렬)
    invalid_comp_ids_path = "data/invalid_composition_ids.json"
    invalid_comp_data = {}
    for comp, ids in sorted(invalid_composition_ids.items(), key=lambda x: -len(x[1])):
        layouts = invalid_composition_layouts.get(comp, set())
        invalid_comp_data[comp] = {
            "design_object_count": len(ids),
            "layout_count": len(layouts),
            "ids": list(ids),
            "layout_ids": list(layouts)
        }
    with open(invalid_comp_ids_path, 'w', encoding='utf-8') as f:
        json.dump(invalid_comp_data, f, ensure_ascii=False, indent=2)
    print(f"📁 invalid 조합별 ID: {invalid_comp_ids_path} ({len(invalid_comp_data)}종류)")
    
    # 전체 조합 요약 CSV 저장 - valid
    valid_summary_path = "data/valid_compositions_summary.csv"
    with open(valid_summary_path, 'w', encoding='utf-8') as f:
        f.write("design_object_count,layout_count,composition\n")
        for comp, ids in sorted(valid_composition_ids.items(), key=lambda x: -len(x[1])):
            layouts = valid_composition_layouts.get(comp, set())
            f.write(f"{len(ids)},{len(layouts)},\"{comp}\"\n")
    print(f"📁 valid 조합 요약: {valid_summary_path} ({len(valid_composition_ids)}종류)")
    
    # 전체 조합 요약 CSV 저장 - invalid
    invalid_summary_path = "data/invalid_compositions_summary.csv"
    with open(invalid_summary_path, 'w', encoding='utf-8') as f:
        f.write("design_object_count,layout_count,composition\n")
        for comp, ids in sorted(invalid_composition_ids.items(), key=lambda x: -len(x[1])):
            layouts = invalid_composition_layouts.get(comp, set())
            f.write(f"{len(ids)},{len(layouts)},\"{comp}\"\n")
    print(f"📁 invalid 조합 요약: {invalid_summary_path} ({len(invalid_composition_ids)}종류)")


if __name__ == "__main__":
    main()
