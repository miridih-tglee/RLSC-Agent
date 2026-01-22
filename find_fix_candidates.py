#!/usr/bin/env python3
"""
변경 대상 Design Object 찾기 (고성능 버전)

DB에서 inference_model_type='agentic'인 design_objects를 분석하여
구조 수정이 필요한 항목들을 찾습니다.

변경 대상 조건:
1. 같은 컨테이너에 Background가 2개 이상
2. Decoration/Marker가 서로 겹침

최적화:
- 멀티프로세싱으로 병렬 분석
- 큰 배치 사이즈로 DB 왕복 최소화
- 스트리밍 방식으로 메모리 효율화

출력: JSON 파일 + 통계
"""

import sys
import json
import time
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from multiprocessing import Pool, cpu_count
from functools import partial

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

OUTPUT_FILE = "data/fix_candidates.json"

# 배치 크기 (메모리 관리) - 더 크게!
BATCH_SIZE = 5000

# 병렬 처리 워커 수
NUM_WORKERS = max(1, cpu_count() - 1)  # CPU 코어 - 1

# 겹침 검사 설정 (전역, CLI로 변경 가능)
OVERLAP_USE_RATIO = False  # True: 작은 박스 대비 비율, False: 단순 겹침
OVERLAP_THRESHOLD = 0.0    # use_ratio=False면 0.0, True면 0.1 권장

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
# 헬퍼 함수들 (process_design_object.py에서 가져옴)
# ============================================================
def get_role(node: Dict) -> str:
    """Role 문자열에서 마지막 부분 추출 (예: 'Role.Element.Background' → 'Background')"""
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


def is_overlapping(bbox1: Tuple, bbox2: Tuple, threshold: float = 0.0, 
                    use_ratio: bool = False) -> bool:
    """
    두 박스가 겹치는지 확인
    
    Args:
        bbox1, bbox2: (x_min, y_min, x_max, y_max)
        threshold: 겹침 임계값
        use_ratio: True면 작은 박스 대비 비율로 계산, False면 단순 면적
    
    Returns:
        use_ratio=False: inter_area > threshold (기본, 조금이라도 겹치면 True)
        use_ratio=True: inter_area / min_area > threshold (작은 박스의 N% 이상 겹쳐야 True)
    """
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2
    
    inter_x_min = max(x1_min, x2_min)
    inter_y_min = max(y1_min, y2_min)
    inter_x_max = min(x1_max, x2_max)
    inter_y_max = min(y1_max, y2_max)
    
    if inter_x_max <= inter_x_min or inter_y_max <= inter_y_min:
        return False
    
    inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)
    
    if use_ratio:
        # 작은 박스 대비 비율로 계산
        area1 = (x1_max - x1_min) * (y1_max - y1_min)
        area2 = (x2_max - x2_min) * (y2_max - y2_min)
        smaller_area = min(area1, area2)
        if smaller_area <= 0:
            return False
        return inter_area / smaller_area > threshold
    else:
        # 단순 면적 비교
        return inter_area > threshold


def should_check_pair(node1: Dict, node2: Dict) -> bool:
    """
    겹침 검사 대상인지 확인
    - Decoration, Marker, Frame, Image가 다른 요소와 겹치면 검사 대상
    - Background만 제외
    """
    role1, role2 = get_role(node1), get_role(node2)
    type1, type2 = get_type(node1), get_type(node2)
    
    # Background는 겹침 허용
    if role1 == 'Background' or role2 == 'Background':
        return False
    
    # Decoration 또는 Marker가 포함되어 있으면 검사
    checkable_roles = ['Decoration', 'Marker']
    if role1 in checkable_roles or role2 in checkable_roles:
        return True
    
    # Frame 또는 Image 타입이 포함되어 있으면 검사
    checkable_types = ['Frame', 'Image']
    if type1 in checkable_types or type2 in checkable_types:
        return True
    
    return False


def is_background(node: Dict) -> bool:
    return get_role(node) == 'Background'


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
# 분석 함수
# ============================================================
def analyze_node(node: Dict, depth: int = 0, path: str = "root") -> List[Dict]:
    """
    노드를 분석하여 문제점을 찾습니다.
    
    반환: 발견된 문제 목록
    [
        {
            "issue_type": "multiple_backgrounds" | "overlapping_decorations",
            "depth": 2,
            "path": "root.children[0].children[1]",
            "details": {...}
        }
    ]
    """
    issues = []
    children = node.get('children', [])
    
    if not children:
        return issues
    
    # 1. Background 중복 검사
    backgrounds = []
    for i, child in enumerate(children):
        if is_background(child):
            backgrounds.append({
                "index": i,
                "id": child.get('id', 'unknown'),
                "type": get_type(child)
            })
    
    if len(backgrounds) > 1:
        issues.append({
            "issue_type": "multiple_backgrounds",
            "depth": depth,
            "path": path,
            "details": {
                "count": len(backgrounds),
                "backgrounds": backgrounds
            }
        })
    
    # 2. Decoration/Marker 겹침 검사
    overlapping_pairs = []
    for i in range(len(children)):
        bbox_i = get_bbox(children[i])
        if not bbox_i:
            continue
        
        for j in range(i + 1, len(children)):
            bbox_j = get_bbox(children[j])
            if not bbox_j:
                continue
            
            if should_check_pair(children[i], children[j]) and is_overlapping(
                bbox_i, bbox_j, threshold=OVERLAP_THRESHOLD, use_ratio=OVERLAP_USE_RATIO
            ):
                overlapping_pairs.append({
                    "indices": [i, j],
                    "elements": [
                        {"id": children[i].get('id', '?'), "role": get_role(children[i])},
                        {"id": children[j].get('id', '?'), "role": get_role(children[j])}
                    ]
                })
    
    if overlapping_pairs:
        issues.append({
            "issue_type": "overlapping_decorations",
            "depth": depth,
            "path": path,
            "details": {
                "pair_count": len(overlapping_pairs),
                "pairs": overlapping_pairs[:5]  # 최대 5개만 저장
            }
        })
    
    # 3. 자식들 재귀 분석
    for i, child in enumerate(children):
        child_path = f"{path}.children[{i}]"
        child_issues = analyze_node(child, depth + 1, child_path)
        issues.extend(child_issues)
    
    return issues


def analyze_structure(structure_json: Dict) -> Dict:
    """
    전체 structure_json을 분석합니다.
    
    반환:
    {
        "needs_fix": True/False,
        "issue_count": 3,
        "max_depth": 2,
        "issues": [...]
    }
    """
    if not structure_json:
        return {"needs_fix": False, "issue_count": 0, "max_depth": 0, "issues": []}
    
    issues = analyze_node(structure_json, depth=0, path="root")
    
    max_depth = 0
    if issues:
        max_depth = max(issue["depth"] for issue in issues)
    
    return {
        "needs_fix": len(issues) > 0,
        "issue_count": len(issues),
        "max_depth": max_depth,
        "issues": issues
    }


# ============================================================
# DB 함수
# ============================================================
def get_total_count() -> int:
    """필터링 조건에 맞는 agentic 개수"""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            # 제외할 role 조건 생성
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
            # 제외할 role 조건 생성
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
def init_worker(use_ratio: bool, threshold: float):
    """워커 프로세스 초기화 - 전역 변수 설정"""
    global OVERLAP_USE_RATIO, OVERLAP_THRESHOLD
    OVERLAP_USE_RATIO = use_ratio
    OVERLAP_THRESHOLD = threshold


def analyze_row(row: Dict) -> Tuple[Optional[Dict], str]:
    """
    단일 row 분석 (병렬 처리용)
    
    반환: (result, status)
        - (None, "no_structure"): structure_json 없음
        - (None, "page_role_skipped"): Page* 패턴으로 스킵
        - (None, "parse_error"): JSON 파싱 실패
        - (None, "no_issue"): 이슈 없음
        - (result, "found"): 이슈 발견
    """
    structure_json = row.get('structure_json')
    if not structure_json:
        return (None, "no_structure")
    
    # structure_json에서 Role.LayoutContainer.Page* 체크
    if has_excluded_structure_role(structure_json):
        return (None, "page_role_skipped")  # 제외 대상 role 패턴이 있으면 스킵
    
    # JSON 파싱
    if isinstance(structure_json, str):
        try:
            structure_json = json.loads(structure_json)
        except:
            return (None, "parse_error")
    
    # 분석
    analysis = analyze_structure(structure_json)
    
    if not analysis["needs_fix"]:
        return (None, "no_issue")
    
    return ({
        "id": row["id"],
        "layout_id": row.get("layout_id"),
        "rlsc_id": row.get("rlsc_id"),
        "design_object_role": row.get("design_object_role"),
        "content_signature_sorted": row.get("content_signature_sorted"),
        "design_object_meta": row.get("design_object_meta"),
        "analysis": {
            "issue_count": analysis["issue_count"],
            "max_depth": analysis["max_depth"],
            "issue_types": list(set(i["issue_type"] for i in analysis["issues"])),
            "issues": analysis["issues"][:5]  # 최대 5개 이슈만 (메모리 절약)
        }
    }, "found")


# ============================================================
# 메인
# ============================================================
def main():
    global OVERLAP_USE_RATIO, OVERLAP_THRESHOLD
    
    # CLI 옵션 파싱
    import argparse
    parser = argparse.ArgumentParser(description='변경 대상 Design Object 찾기')
    parser.add_argument('--use-ratio', action='store_true',
                        help='겹침 검사 시 작은 박스 대비 비율 사용 (기본: 단순 겹침)')
    parser.add_argument('--threshold', type=float, default=None,
                        help='겹침 임계값 (use-ratio 시 기본 0.1, 아니면 0.0)')
    parser.add_argument('--output', type=str, default=OUTPUT_FILE,
                        help=f'출력 파일 경로 (기본: {OUTPUT_FILE})')
    args = parser.parse_args()
    
    # 겹침 설정 적용
    OVERLAP_USE_RATIO = args.use_ratio
    if args.threshold is not None:
        OVERLAP_THRESHOLD = args.threshold
    else:
        OVERLAP_THRESHOLD = 0.1 if OVERLAP_USE_RATIO else 0.0
    
    print("=" * 60)
    print("변경 대상 Design Object 찾기 (고성능 버전)")
    print("=" * 60)
    print(f"⚡ 병렬 처리: {NUM_WORKERS} workers")
    print(f"📦 배치 크기: {BATCH_SIZE:,}")
    print(f"🔍 겹침 검사: {'작은 박스 대비 비율' if OVERLAP_USE_RATIO else '단순 겹침'} (threshold={OVERLAP_THRESHOLD})")
    print(f"\n📋 필터링 조건:")
    print(f"   - max_depth: {MIN_DEPTH} ~ {MAX_DEPTH}")
    print(f"   - 제외 design_object_role: {', '.join(r.split('.')[-1] for r in EXCLUDED_ROLES)}")
    print(f"   - 제외 structure_json role 패턴: {EXCLUDED_STRUCTURE_ROLE_PREFIX}*")
    
    start_time = time.time()
    
    # 전체 개수 확인
    total_count = get_total_count()
    print(f"\n📊 전체 agentic design_objects: {total_count:,}개")
    
    # 예상 시간 계산 (대략 초당 5000개 처리 가정)
    estimated_minutes = total_count / 5000 / 60
    print(f"⏱️  예상 소요 시간: 약 {estimated_minutes:.1f}분")
    
    # 결과 저장용
    candidates = []
    issue_type_counts = defaultdict(int)
    depth_counts = defaultdict(int)
    status_counts = defaultdict(int)  # 상태별 카운트 (Page* 스킵 등)
    
    # 배치 처리 with 병렬 분석
    processed = 0
    offset = 0
    
    with Pool(NUM_WORKERS, initializer=init_worker, 
               initargs=(OVERLAP_USE_RATIO, OVERLAP_THRESHOLD)) as pool:
        while offset < total_count:
            batch_start = time.time()
            
            # DB에서 배치 가져오기
            batch = fetch_design_objects_batch(offset, BATCH_SIZE)
            if not batch:
                break
            
            # 병렬 분석
            results = pool.map(analyze_row, batch)
            
            # 결과 수집
            for result, status in results:
                status_counts[status] += 1
                
                if result:
                    # 이슈 타입별 카운트
                    for issue_type in result["analysis"]["issue_types"]:
                        issue_type_counts[issue_type] += 1
                    depth_counts[result["analysis"]["max_depth"]] += 1
                    
                    candidates.append(result)
            
            processed += len(batch)
            batch_time = time.time() - batch_start
            speed = len(batch) / batch_time if batch_time > 0 else 0
            eta = (total_count - processed) / speed / 60 if speed > 0 else 0
            
            page_skipped = status_counts.get("page_role_skipped", 0)
            print(f"  ✅ {processed:,}/{total_count:,} ({processed*100/total_count:.1f}%) "
                  f"| 속도: {speed:.0f}/s | 남은 시간: {eta:.1f}분 "
                  f"| Page* 스킵: {page_skipped:,}개 | 후보: {len(candidates):,}개")
            
            offset += BATCH_SIZE
    
    elapsed = time.time() - start_time
    
    # 결과 저장
    result = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "processing_time_seconds": round(elapsed, 1),
            "filter_config": {
                "max_depth_range": {"min": MIN_DEPTH, "max": MAX_DEPTH},
                "excluded_design_object_roles": EXCLUDED_ROLES,
                "excluded_structure_role_prefix": EXCLUDED_STRUCTURE_ROLE_PREFIX
            },
            "overlap_config": {
                "use_ratio": OVERLAP_USE_RATIO,
                "threshold": OVERLAP_THRESHOLD,
                "description": "작은 박스 대비 비율" if OVERLAP_USE_RATIO else "단순 겹침 (면적 > threshold)"
            },
            "statistics": {
                "total_db_filtered": total_count,
                "page_role_skipped": status_counts.get("page_role_skipped", 0),
                "actually_analyzed": total_count - status_counts.get("page_role_skipped", 0) - status_counts.get("no_structure", 0) - status_counts.get("parse_error", 0),
                "needs_fix_count": len(candidates),
                "no_issue_count": status_counts.get("no_issue", 0),
                "fix_ratio": f"{len(candidates)*100/total_count:.2f}%" if total_count > 0 else "0%",
                "status_counts": dict(status_counts),
                "issue_type_counts": dict(issue_type_counts),
                "depth_distribution": dict(sorted(depth_counts.items()))
            }
        },
        "candidates": candidates
    }
    
    # JSON 저장
    output_path = args.output
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    # 결과 출력
    page_skipped = status_counts.get("page_role_skipped", 0)
    no_structure = status_counts.get("no_structure", 0)
    parse_error = status_counts.get("parse_error", 0)
    no_issue = status_counts.get("no_issue", 0)
    actually_analyzed = total_count - page_skipped - no_structure - parse_error
    
    print("\n" + "=" * 60)
    print("📋 결과 요약")
    print("=" * 60)
    print(f"\n⏱️  총 소요 시간: {elapsed/60:.1f}분 ({elapsed:.0f}초)")
    print(f"🚀 처리 속도: {total_count/elapsed:.0f}개/초")
    
    print(f"\n📊 처리 통계:")
    print(f"   1. DB 필터링 후 (depth {MIN_DEPTH}~{MAX_DEPTH}, role 제외): {total_count:,}개")
    print(f"   2. Page* 패턴 스킵: {page_skipped:,}개")
    if no_structure > 0:
        print(f"   3. structure_json 없음: {no_structure:,}개")
    if parse_error > 0:
        print(f"   4. JSON 파싱 실패: {parse_error:,}개")
    print(f"   → 실제 분석 대상: {actually_analyzed:,}개")
    print(f"   → 이슈 없음: {no_issue:,}개")
    print(f"   → 변경 대상: {len(candidates):,}개 ({len(candidates)*100/actually_analyzed:.2f}%)" if actually_analyzed > 0 else "   → 변경 대상: 0개")
    
    print(f"\n📁 저장 위치: {output_path}")
    
    print("\n📊 이슈 타입별 분포:")
    for issue_type, count in sorted(issue_type_counts.items()):
        print(f"  - {issue_type}: {count:,}건")
    
    print("\n📊 깊이(Depth)별 분포:")
    for depth, count in sorted(depth_counts.items()):
        print(f"  - depth {depth}: {count:,}건")
    
    # 샘플 출력
    if candidates:
        print("\n📝 샘플 (처음 5개):")
        for c in candidates[:5]:
            print(f"  - ID: {c['id']}, Issues: {c['analysis']['issue_types']}, "
                  f"Depth: {c['analysis']['max_depth']}")


if __name__ == "__main__":
    main()
