#!/usr/bin/env python3
"""
필터링된 Design Object 추출기

DB에서 필터링 조건에 맞는 design_objects를 조회하고,
Role.LayoutContainer.Page*가 없는 항목만 JSON으로 저장합니다.

필터링 조건:
1. inference_model_type = 'agentic'
2. design_object_role NOT IN (Opening, Agenda, SectionDivider, Ending, Content)
3. design_object_meta의 max_depth가 4~8 범위
4. structure_json에 Role.LayoutContainer.Page*가 없음

출력: JSON 파일
"""

import sys
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
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

OUTPUT_FILE = "data/filtered_objects.json"

# 배치 크기 (메모리 관리)
BATCH_SIZE = 5000

# 병렬 처리 워커 수
NUM_WORKERS = max(1, cpu_count() - 1)

# max_depth 필터링 설정
MIN_DEPTH = 4
MAX_DEPTH = 8

# 제외할 design_object_role 목록
EXCLUDED_ROLES = [
    'Role.Page.Opening',
    'Role.Page.Agenda',
    'Role.Page.SectionDivider',
    'Role.Page.Ending',
    'Role.Page.Content'
]

# structure_json에서 제외할 role 패턴 (이 패턴으로 시작하면 제외)
EXCLUDED_STRUCTURE_ROLE_PREFIX = 'Role.LayoutContainer.Page'


# ============================================================
# 헬퍼 함수
# ============================================================
def has_excluded_structure_role(structure_json) -> bool:
    """
    structure_json에서 제외 대상 role 패턴(Role.LayoutContainer.Page*)이 있는지 재귀적으로 확인
    
    Args:
        structure_json: JSON 문자열 또는 파싱된 dict/list
        
    Returns:
        True: 제외 대상 role 패턴이 발견됨 (이 항목은 제외해야 함)
        False: 제외 대상 role 패턴이 없음
    """
    # JSON 문자열이면 파싱
    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return False
    
    if structure_json is None:
        return False
    
    def check_node(node):
        """노드와 하위 노드들을 재귀적으로 확인"""
        if isinstance(node, dict):
            # 현재 노드의 role 확인
            role = node.get('role', '')
            if isinstance(role, str) and role.startswith(EXCLUDED_STRUCTURE_ROLE_PREFIX):
                return True
            
            # children 확인
            children = node.get('children', [])
            for child in children:
                if check_node(child):
                    return True
                    
        elif isinstance(node, list):
            # 리스트인 경우 각 항목 확인
            for item in node:
                if check_node(item):
                    return True
        
        return False
    
    return check_node(structure_json)


# ============================================================
# DB 함수
# ============================================================
def get_total_count() -> int:
    """필터링 조건에 맞는 agentic 개수"""
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
    """배치로 design_objects 조회 (필터링 조건 적용)"""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            role_placeholders = ', '.join(['%s'] * len(EXCLUDED_ROLES))
            
            cur.execute(f"""
                SELECT 
                    id,
                    layout_id,
                    content_signature_sorted,
                    design_object_meta,
                    design_object_role,
                    rlsc_id,
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
# 병렬 처리용 워커 함수
# ============================================================
def process_row(row: Dict) -> Optional[Dict]:
    """
    단일 row 처리 (병렬 처리용)
    
    - structure_json이 없으면 스킵
    - Role.LayoutContainer.Page*가 있으면 스킵
    - JSON 파싱 후 반환
    """
    structure_json = row.get('structure_json')
    if not structure_json:
        return None
    
    # structure_json에서 Role.LayoutContainer.Page* 체크
    if has_excluded_structure_role(structure_json):
        return None  # 제외 대상 role 패턴이 있으면 스킵
    
    # JSON 파싱
    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return None
    
    # 결과 반환
    return {
        "id": row["id"],
        "layout_id": row.get("layout_id"),
        "rlsc_id": row.get("rlsc_id"),
        "design_object_role": row.get("design_object_role"),
        "content_signature_sorted": row.get("content_signature_sorted"),
        "design_object_meta": row.get("design_object_meta"),
        "structure_json": structure_json
    }


# ============================================================
# 메인
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='필터링된 Design Object 추출')
    parser.add_argument('--output', type=str, default=OUTPUT_FILE,
                        help=f'출력 파일 경로 (기본: {OUTPUT_FILE})')
    parser.add_argument('--limit', type=int, default=None,
                        help='최대 추출 개수 (기본: 전체)')
    args = parser.parse_args()
    
    print("=" * 60)
    print("필터링된 Design Object 추출기")
    print("=" * 60)
    print(f"⚡ 병렬 처리: {NUM_WORKERS} workers")
    print(f"📦 배치 크기: {BATCH_SIZE:,}")
    print(f"\n📋 필터링 조건:")
    print(f"   - max_depth: {MIN_DEPTH} ~ {MAX_DEPTH}")
    print(f"   - 제외 design_object_role: {', '.join(r.split('.')[-1] for r in EXCLUDED_ROLES)}")
    print(f"   - 제외 structure_json role 패턴: {EXCLUDED_STRUCTURE_ROLE_PREFIX}*")
    
    start_time = time.time()
    
    # 전체 개수 확인
    total_count = get_total_count()
    print(f"\n📊 DB 필터링 후 agentic design_objects: {total_count:,}개")
    
    if args.limit:
        total_count = min(total_count, args.limit)
        print(f"📊 최대 추출 개수 제한: {args.limit:,}개")
    
    # 결과 저장용
    results = []
    skipped_page_role = 0
    
    # 배치 처리 with 병렬 분석
    processed = 0
    offset = 0
    
    with Pool(NUM_WORKERS) as pool:
        while offset < total_count:
            batch_start = time.time()
            
            # DB에서 배치 가져오기
            batch = fetch_design_objects_batch(offset, BATCH_SIZE)
            if not batch:
                break
            
            # 병렬 처리
            batch_results = pool.map(process_row, batch)
            
            # 결과 수집
            for result in batch_results:
                if result:
                    results.append(result)
                else:
                    skipped_page_role += 1
            
            processed += len(batch)
            batch_time = time.time() - batch_start
            speed = len(batch) / batch_time if batch_time > 0 else 0
            eta = (total_count - processed) / speed / 60 if speed > 0 else 0
            
            print(f"  ✅ {processed:,}/{total_count:,} ({processed*100/total_count:.1f}%) "
                  f"| 속도: {speed:.0f}/s | 남은 시간: {eta:.1f}분 "
                  f"| 추출: {len(results):,}개 | Page* 스킵: {skipped_page_role:,}개")
            
            offset += BATCH_SIZE
            
            # limit 체크
            if args.limit and processed >= args.limit:
                break
    
    elapsed = time.time() - start_time
    
    # 결과 저장
    output_data = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "processing_time_seconds": round(elapsed, 1),
            "filter_config": {
                "max_depth_range": {"min": MIN_DEPTH, "max": MAX_DEPTH},
                "excluded_design_object_roles": EXCLUDED_ROLES,
                "excluded_structure_role_prefix": EXCLUDED_STRUCTURE_ROLE_PREFIX
            },
            "statistics": {
                "total_db_filtered": total_count,
                "extracted_count": len(results),
                "skipped_page_role_count": skipped_page_role,
                "extraction_ratio": f"{len(results)*100/processed:.2f}%" if processed > 0 else "0%"
            }
        },
        "objects": results
    }
    
    # JSON 저장
    output_path = args.output
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("📋 결과 요약")
    print("=" * 60)
    print(f"\n⏱️  총 소요 시간: {elapsed/60:.1f}분 ({elapsed:.0f}초)")
    print(f"🚀 처리 속도: {processed/elapsed:.0f}개/초" if elapsed > 0 else "")
    print(f"\n📊 통계:")
    print(f"   - DB 필터링 후 전체: {total_count:,}개")
    print(f"   - Page* 패턴으로 스킵: {skipped_page_role:,}개")
    print(f"   - 최종 추출: {len(results):,}개 ({len(results)*100/processed:.2f}%)" if processed > 0 else "")
    print(f"\n📁 저장 위치: {output_path}")
    
    # 샘플 출력
    if results:
        print("\n📝 샘플 (처음 3개):")
        for obj in results[:3]:
            print(f"  - ID: {obj['id']}, layout_id: {obj.get('layout_id')}, "
                  f"role: {obj.get('design_object_role', 'N/A')}")


if __name__ == "__main__":
    main()
