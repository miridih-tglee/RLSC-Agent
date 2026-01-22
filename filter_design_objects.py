#!/usr/bin/env python3
"""
design_objects 테이블에서 조건에 맞는 데이터를 필터링하는 스크립트

사용법:
    아래 설정 변수를 수정한 후 실행
    python filter_design_objects.py
"""

import sys
import json
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2가 설치되어 있지 않습니다. 설치해주세요:")
    print("  pip install psycopg2-binary")
    sys.exit(1)

# ============================================================
# 🔧 설정 변수 (여기서 수정하세요)
# ============================================================

# max_depth 범위
MIN_DEPTH = 4
MAX_DEPTH = 8

# element_count 범위
MIN_ELEMENTS = 0
MAX_ELEMENTS = 10

# 출력 파일명
OUTPUT_FILE = "filtered_results_depth_4_8_elements_0_10.json"

# ============================================================
# DB 연결 정보
# ============================================================
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54322,
    "user": "postgres",
    "password": "postgres",
    "dbname": "postgres"
}


def get_total_count(conn) -> int:
    """전체 design_objects 개수 조회"""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM design_objects")
        return cur.fetchone()[0]


def get_agentic_count(conn) -> int:
    """inference_model_type = 'agentic'인 개수 조회"""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM design_objects WHERE inference_model_type = 'agentic'")
        return cur.fetchone()[0]


def filter_design_objects(
    min_depth: int,
    max_depth: int,
    min_elements: int,
    max_elements: int
) -> tuple[list, dict]:
    """조건에 맞는 design_objects 필터링"""
    
    conn = psycopg2.connect(**DB_CONFIG)
    
    try:
        # 통계 정보
        total_count = get_total_count(conn)
        agentic_count = get_agentic_count(conn)
        
        # 필터링 쿼리
        query = """
            SELECT 
                id,
                uuid,
                design_object_role,
                design_object_meta,
                structure_json,
                content_signature_sorted,
                origin_size_thumbnail_url,
                created_at,
                updated_at
            FROM design_objects
            WHERE 
                inference_model_type = 'agentic'
                AND design_object_meta IS NOT NULL
                AND (design_object_meta->'structure'->>'max_depth')::int >= %s
                AND (design_object_meta->'structure'->>'max_depth')::int <= %s
                AND (design_object_meta->'structure'->>'element_count')::int >= %s
                AND (design_object_meta->'structure'->>'element_count')::int <= %s
            ORDER BY id DESC
        """
        
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (min_depth, max_depth, min_elements, max_elements))
            results = cur.fetchall()
        
        # 결과 변환
        filtered_results = []
        for row in results:
            item = dict(row)
            # datetime 객체를 문자열로 변환
            if item.get('created_at'):
                item['created_at'] = item['created_at'].isoformat()
            if item.get('updated_at'):
                item['updated_at'] = item['updated_at'].isoformat()
            # uuid를 문자열로 변환
            if item.get('uuid'):
                item['uuid'] = str(item['uuid'])
            filtered_results.append(item)
        
        stats = {
            "total_design_objects": total_count,
            "total_agentic": agentic_count,
            "filtered_count": len(filtered_results),
            "filter_conditions": {
                "inference_model_type": "agentic",
                "max_depth": {"min": min_depth, "max": max_depth},
                "element_count": {"min": min_elements, "max": max_elements}
            }
        }
        
        return filtered_results, stats
        
    finally:
        conn.close()


def main():
    print(f"\n🔍 필터링 조건:")
    print(f"   - inference_model_type = 'agentic'")
    print(f"   - max_depth: {MIN_DEPTH} ~ {MAX_DEPTH}")
    print(f"   - element_count: {MIN_ELEMENTS} ~ {MAX_ELEMENTS}")
    print()
    
    # 필터링 실행
    results, stats = filter_design_objects(
        min_depth=MIN_DEPTH,
        max_depth=MAX_DEPTH,
        min_elements=MIN_ELEMENTS,
        max_elements=MAX_ELEMENTS
    )
    
    # 통계 출력
    print(f"📊 통계:")
    print(f"   - 전체 design_objects: {stats['total_design_objects']:,}개")
    print(f"   - inference_model_type='agentic': {stats['total_agentic']:,}개")
    print(f"   - 필터링 결과: {stats['filtered_count']:,}개")
    print(f"   - 비율: {stats['filtered_count'] / stats['total_design_objects'] * 100:.2f}% (전체 대비)")
    if stats['total_agentic'] > 0:
        print(f"   - 비율: {stats['filtered_count'] / stats['total_agentic'] * 100:.2f}% (agentic 대비)")
    print()
    
    # 결과 저장
    output_path = Path(__file__).parent / "data" / OUTPUT_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    output_data = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "statistics": stats
        },
        "results": results
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 결과 저장: {output_path}")
    
    # 샘플 출력
    if results:
        print(f"\n📋 샘플 데이터 (처음 3개):")
        for i, item in enumerate(results[:3], 1):
            meta = item.get('design_object_meta', {}).get('structure', {})
            print(f"   {i}. id={item['id']}, max_depth={meta.get('max_depth')}, element_count={meta.get('element_count')}")


if __name__ == "__main__":
    main()
