"""
스마트블록 적합성 판단 - 룰베이스 버전
content_signature.json을 분석하여 LLM 없이 스마트블록 적합성 판단

핵심 로직:
1. 컨테이너(Grid, HStack, VStack)의 children이 2개 이상인지 확인
2. children의 구조적 시그니처가 동일하거나 유사한지 비교
3. 동일/유사한 구조가 반복되면 스마트블록 적합

v2: 유사도 기반 비교 추가 (완전 일치 + 유사 구조 모두 인식)
v3: DB 연동 - valid_container_ids.json 입력 지원
"""

import json
import hashlib
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from collections import Counter
from itertools import combinations
import argparse

# DB 설정 (process_design_object.py와 동일)
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54322,
    "user": "postgres",
    "password": "postgres",
    "dbname": "postgres"
}


def get_db_connection():
    """DB 연결 생성"""
    try:
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(**DB_CONFIG)
    except ImportError:
        print("❌ psycopg2가 설치되어 있지 않습니다. pip install psycopg2-binary")
        sys.exit(1)


def fetch_content_signature_from_db(object_id: int) -> Optional[List[Dict]]:
    """DB에서 content_signature 조회"""
    import psycopg2.extras
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT content_signature FROM design_objects WHERE id = %s",
                (object_id,)
            )
            result = cur.fetchone()
            if result and result.get("content_signature"):
                return result["content_signature"]
            return None
    finally:
        conn.close()


def fetch_content_signatures_batch(object_ids: List[int], batch_size: int = 500) -> Dict[int, List[Dict]]:
    """DB에서 여러 content_signature 일괄 조회"""
    import psycopg2.extras
    results = {}
    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for i in range(0, len(object_ids), batch_size):
                batch = object_ids[i:i + batch_size]
                cur.execute(
                    "SELECT id, content_signature FROM design_objects WHERE id = ANY(%s)",
                    (batch,)
                )
                for row in cur.fetchall():
                    if row.get("content_signature"):
                        results[row["id"]] = row["content_signature"]
    finally:
        conn.close()

    return results


@dataclass
class SmartBlockResult:
    """스마트블록 적합성 판단 결과"""
    is_eligible: bool
    score: int  # 1-10
    repeatable_count: int
    repeatable_type: str
    container_type: str
    pattern_description: str
    details: Dict[str, Any]


def get_structure_signature(node: Dict[str, Any], depth: int = 0, max_depth: int = 3) -> str:
    """
    노드의 구조적 시그니처를 생성
    type과 role만 사용하여 구조 비교 (textCapacity 등 세부 값은 무시)
    """
    if depth > max_depth:
        return "..."

    node_type = node.get("type", "Unknown")
    node_role = node.get("role", "null")

    children = node.get("children", [])
    if children:
        child_sigs = [get_structure_signature(c, depth + 1, max_depth) for c in children]
        children_str = ",".join(child_sigs)
        return f"{node_role}:{node_type}[{children_str}]"
    else:
        return f"{node_role}:{node_type}"


def get_structure_hash(node: Dict[str, Any], max_depth: int = 3) -> str:
    """구조 시그니처의 해시값 반환 (비교용)"""
    sig = get_structure_signature(node, max_depth=max_depth)
    return hashlib.md5(sig.encode()).hexdigest()[:8]


# ============================================================
# 유사도 기반 비교 (v2)
# ============================================================

def get_skeleton_signature(node: Dict[str, Any], depth: int = 0, max_depth: int = 2) -> str:
    """
    스켈레톤 시그니처: children 개수 무시, 타입만 비교
    예: VStack[Title:Text, Description:Text, Description:Text]
        → VStack[Title:Text, Description:Text]  (중복 제거)
    """
    if depth > max_depth:
        return "..."

    node_type = node.get("type", "Unknown")
    node_role = node.get("role", "null")

    children = node.get("children", [])
    if children:
        # 각 child의 스켈레톤 추출 후 중복 제거 (순서 유지)
        child_sigs = []
        seen = set()
        for c in children:
            sig = get_skeleton_signature(c, depth + 1, max_depth)
            if sig not in seen:
                child_sigs.append(sig)
                seen.add(sig)
        children_str = ",".join(child_sigs)
        return f"{node_role}:{node_type}[{children_str}]"
    else:
        return f"{node_role}:{node_type}"


def count_leaf_nodes(node: Dict[str, Any]) -> int:
    """
    노드 내의 leaf 노드(Text, SVG, Image, Frame) 개수를 셈
    작은 라벨 그룹(예: SVG + Text = 2개)을 걸러내기 위한 필터용
    """
    leaf_types = {"Text", "SVG", "Image", "Frame"}
    node_type = node.get("type", "")
    children = node.get("children", [])

    if node_type in leaf_types:
        return 1

    count = 0
    for child in children:
        count += count_leaf_nodes(child)
    return count


def get_feature_set(node: Dict[str, Any], depth: int = 0, max_depth: int = 2) -> Set[str]:
    """
    노드의 특징 집합 추출: (depth, role, type) 튜플들의 집합
    구조가 약간 달라도 핵심 요소가 같으면 유사하다고 판단
    """
    features = set()

    if depth > max_depth:
        return features

    node_type = node.get("type", "Unknown")
    node_role = node.get("role") or "null"

    # 현재 노드의 특징 추가
    features.add(f"d{depth}:{node_role}:{node_type}")

    # 자식 노드들의 특징도 추가
    for child in node.get("children", []):
        features.update(get_feature_set(child, depth + 1, max_depth))

    return features


def calculate_similarity(node1: Dict[str, Any], node2: Dict[str, Any]) -> float:
    """
    두 노드 간의 구조적 유사도 계산 (Jaccard similarity)
    0.0 ~ 1.0 반환
    """
    features1 = get_feature_set(node1)
    features2 = get_feature_set(node2)

    if not features1 and not features2:
        return 1.0
    if not features1 or not features2:
        return 0.0

    intersection = features1 & features2
    union = features1 | features2

    return len(intersection) / len(union)


def find_similar_groups(children: List[Dict[str, Any]], threshold: float = 0.7) -> List[List[int]]:
    """
    유사한 children끼리 그룹화
    threshold: 유사도 임계값 (0.7 = 70% 이상 유사하면 같은 그룹)

    Returns: 그룹별 인덱스 리스트 (예: [[0, 1, 2], [3, 4]])
    """
    n = len(children)
    if n < 2:
        return []

    # 유사도 매트릭스 계산
    similarity_matrix = {}
    for i, j in combinations(range(n), 2):
        sim = calculate_similarity(children[i], children[j])
        similarity_matrix[(i, j)] = sim
        similarity_matrix[(j, i)] = sim

    # Union-Find로 그룹화
    parent = list(range(n))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # 유사도가 threshold 이상인 쌍 합치기
    for (i, j), sim in similarity_matrix.items():
        if i < j and sim >= threshold:
            union(i, j)

    # 그룹별로 정리
    groups = {}
    for i in range(n):
        root = find(i)
        if root not in groups:
            groups[root] = []
        groups[root].append(i)

    # 2개 이상인 그룹만 반환
    return [indices for indices in groups.values() if len(indices) >= 2]


def find_repeating_containers(node: Dict[str, Any], path: str = "root", use_similarity: bool = True, min_leaf_count: int = 3) -> List[Dict[str, Any]]:
    """
    반복되는 children을 가진 컨테이너를 찾음

    Args:
        use_similarity: True면 유사도 기반 비교도 사용 (약간 다른 구조도 인식)
        min_leaf_count: 최소 leaf 노드 수 (기본 3). 작은 라벨 그룹 필터링

    핵심: children 중 **컨테이너 타입**(VStack, HStack, ZStack, Group, Grid)만 반복 체크
          leaf 노드(Text, SVG, Image)의 반복은 스마트블록에 의미 없음
          ⭐ 추가: leaf 노드가 min_leaf_count 미만인 작은 컨테이너는 무시
    """
    results = []

    node_type = node.get("type", "")
    children = node.get("children", [])

    # 컨테이너 타입 정의
    container_types = {"Grid", "HStack", "VStack", "ZStack", "Group"}

    if node_type in container_types and len(children) >= 2:
        # ⭐ 핵심: 컨테이너 타입인 children만 필터링
        # ⭐ 추가: leaf 노드가 min_leaf_count 이상인 컨테이너만 포함
        container_children = []
        container_indices = []
        for i, child in enumerate(children):
            child_type = child.get("type", "")
            if child_type in container_types:
                leaf_count = count_leaf_nodes(child)
                if leaf_count >= min_leaf_count:
                    container_children.append(child)
                    container_indices.append(i)

        # 컨테이너 children이 2개 이상일 때만 반복 체크
        if len(container_children) >= 2:
            # 각 컨테이너 child의 구조 시그니처 계산
            child_signatures = []
            for idx, child in zip(container_indices, container_children):
                sig = get_structure_signature(child)
                sig_hash = get_structure_hash(child)
                skeleton = get_skeleton_signature(child)
                skeleton_hash = hashlib.md5(skeleton.encode()).hexdigest()[:8]
                child_signatures.append({
                    "index": idx,
                    "signature": sig,
                    "hash": sig_hash,
                    "skeleton": skeleton,
                    "skeleton_hash": skeleton_hash,
                    "type": child.get("type", "Unknown"),
                    "role": child.get("role", "null")
                })

            best_result = None

            # 방법 1: 완전 일치 (기존 방식)
            hash_counter = Counter(cs["hash"] for cs in child_signatures)
            most_common_hash, exact_count = hash_counter.most_common(1)[0]

            if exact_count >= 2:
                repeating_children = [cs for cs in child_signatures if cs["hash"] == most_common_hash]
                best_result = {
                    "path": path,
                    "container_type": node_type,
                    "container_role": node.get("role", "null"),
                    "total_children": len(container_children),
                    "repeating_count": exact_count,
                    "repeating_ratio": exact_count / len(container_children),
                    "repeating_children": repeating_children,
                    "sample_signature": repeating_children[0]["signature"] if repeating_children else "",
                    "child_type": repeating_children[0]["type"] if repeating_children else "Unknown",
                    "child_role": repeating_children[0]["role"] if repeating_children else "null",
                    "match_type": "exact",  # 완전 일치
                    "similarity_score": 1.0,
                }

            # 방법 2: 스켈레톤 일치 (중복 요소 무시)
            skeleton_counter = Counter(cs["skeleton_hash"] for cs in child_signatures)
            most_common_skeleton, skeleton_count = skeleton_counter.most_common(1)[0]

            if skeleton_count > exact_count and skeleton_count >= 2:
                repeating_children = [cs for cs in child_signatures if cs["skeleton_hash"] == most_common_skeleton]
                best_result = {
                    "path": path,
                    "container_type": node_type,
                    "container_role": node.get("role", "null"),
                    "total_children": len(container_children),
                    "repeating_count": skeleton_count,
                    "repeating_ratio": skeleton_count / len(container_children),
                    "repeating_children": repeating_children,
                    "sample_signature": repeating_children[0]["signature"] if repeating_children else "",
                    "child_type": repeating_children[0]["type"] if repeating_children else "Unknown",
                    "child_role": repeating_children[0]["role"] if repeating_children else "null",
                    "match_type": "skeleton",  # 스켈레톤 일치
                    "similarity_score": 0.9,
                }

            # 방법 3: 유사도 기반 그룹화 (threshold 70%)
            if use_similarity:
                # ⭐ 컨테이너 children만 사용
                similar_groups = find_similar_groups(container_children, threshold=0.7)
                if similar_groups:
                    largest_group = max(similar_groups, key=len)
                    similarity_count = len(largest_group)

                    # 그룹 내 평균 유사도 계산
                    if similarity_count >= 2:
                        sims = []
                        for i, j in combinations(largest_group, 2):
                            sims.append(calculate_similarity(container_children[i], container_children[j]))
                        avg_similarity = sum(sims) / len(sims) if sims else 0

                        # 유사도 기반 결과가 더 나은 경우에만 사용
                        if best_result is None or similarity_count > best_result["repeating_count"]:
                            repeating_children = [child_signatures[i] for i in largest_group]
                            best_result = {
                                "path": path,
                                "container_type": node_type,
                                "container_role": node.get("role", "null"),
                                "total_children": len(container_children),
                                "repeating_count": similarity_count,
                                "repeating_ratio": similarity_count / len(container_children),
                                "repeating_children": repeating_children,
                                "sample_signature": repeating_children[0]["signature"] if repeating_children else "",
                                "child_type": repeating_children[0]["type"] if repeating_children else "Unknown",
                                "child_role": repeating_children[0]["role"] if repeating_children else "null",
                                "match_type": "similar",  # 유사도 기반
                                "similarity_score": avg_similarity,
                            }

            if best_result:
                results.append(best_result)

    # 재귀적으로 children 탐색
    for i, child in enumerate(children):
        child_path = f"{path}.{node_type}[{i}]"
        results.extend(find_repeating_containers(child, child_path, use_similarity, min_leaf_count))

    return results


def classify_pattern(container: Dict[str, Any]) -> str:
    """반복 패턴 분류"""
    child_sig = container.get("sample_signature", "")
    child_type = container.get("child_type", "")
    child_role = container.get("child_role", "")
    container_type = container.get("container_type", "")

    # 패턴 분류 규칙
    if "Image" in child_sig or "Frame" in child_sig:
        if "Title" in child_sig and ("Description" in child_sig or "Subtitle" in child_sig):
            return "팀원/프로필 카드"
        elif "Title" in child_sig:
            return "이미지+텍스트 카드"
        else:
            return "이미지 그리드"

    if "SVG" in child_sig:
        if "Title" in child_sig and "Description" in child_sig:
            return "아이콘+텍스트 카드"
        elif "Title" in child_sig:
            return "아이콘+제목 리스트"
        else:
            return "아이콘/장식 그리드"

    if child_type in {"VStack", "HStack"}:
        if "Title" in child_sig and "Description" in child_sig:
            return "정보 카드 그리드"
        elif "Title" in child_sig:
            return "제목 리스트"
        else:
            return "컨텐츠 블록 그리드"

    if child_type == "Text":
        return "텍스트 리스트"

    return "일반 반복 요소"


def evaluate_smartblock_eligibility(content_signature: List[Dict[str, Any]], min_leaf_count: int = 3) -> SmartBlockResult:
    """
    content_signature.json을 분석하여 스마트블록 적합성 판단

    Args:
        min_leaf_count: 최소 leaf 노드 수 (기본 3). 작은 라벨 그룹 필터링
    """
    all_repeating = []

    # 모든 루트 노드에서 반복 패턴 찾기
    for root_node in content_signature:
        repeating = find_repeating_containers(root_node, min_leaf_count=min_leaf_count)
        all_repeating.extend(repeating)

    if not all_repeating:
        return SmartBlockResult(
            is_eligible=False,
            score=1,
            repeatable_count=0,
            repeatable_type="없음",
            container_type="없음",
            pattern_description="반복되는 요소가 없음",
            details={"repeating_containers": []}
        )

    # 가장 유의미한 반복 패턴 선택 (반복 횟수 * 깊이 가중치)
    best_container = max(all_repeating, key=lambda x: (
        x["repeating_count"],  # 반복 횟수 우선
        x["repeating_ratio"],  # 비율
        -len(x["path"].split("."))  # 얕은 깊이 선호
    ))

    repeating_count = best_container["repeating_count"]
    repeating_ratio = best_container["repeating_ratio"]
    container_type = best_container["container_type"]
    pattern_type = classify_pattern(best_container)

    # 점수 계산
    score = 1

    # 반복 횟수 기반 점수 (2개: +2, 3개: +3, 4개+: +4)
    if repeating_count >= 4:
        score += 4
    elif repeating_count >= 3:
        score += 3
    else:
        score += 2

    # 반복 비율 기반 점수 (100%: +3, 80%+: +2, 50%+: +1)
    if repeating_ratio >= 1.0:
        score += 3
    elif repeating_ratio >= 0.8:
        score += 2
    elif repeating_ratio >= 0.5:
        score += 1

    # Grid 컨테이너 보너스 (+1)
    if container_type == "Grid":
        score += 1

    # 의미있는 패턴 보너스 (+1)
    meaningful_patterns = {"팀원/프로필 카드", "아이콘+텍스트 카드", "정보 카드 그리드", "이미지+텍스트 카드"}
    if pattern_type in meaningful_patterns:
        score += 1

    score = min(10, score)  # 최대 10점

    is_eligible = score >= 5 and repeating_count >= 2

    return SmartBlockResult(
        is_eligible=is_eligible,
        score=score,
        repeatable_count=repeating_count,
        repeatable_type=pattern_type,
        container_type=container_type,
        pattern_description=f"{container_type} 안에 {repeating_count}개의 동일한 {pattern_type} 반복",
        details={
            "repeating_containers": all_repeating,
            "best_container": best_container,
            "total_patterns_found": len(all_repeating)
        }
    )


def analyze_folder(folder_path: Path, min_leaf_count: int = 3) -> Optional[Dict[str, Any]]:
    """단일 폴더 분석"""
    content_sig_path = folder_path / "content_signature.json"

    if not content_sig_path.exists():
        return None

    try:
        with open(content_sig_path, "r", encoding="utf-8") as f:
            content_signature = json.load(f)

        result = evaluate_smartblock_eligibility(content_signature, min_leaf_count=min_leaf_count)

        return {
            "folder": folder_path.name,
            "is_eligible": result.is_eligible,
            "score": result.score,
            "repeatable_count": result.repeatable_count,
            "repeatable_type": result.repeatable_type,
            "container_type": result.container_type,
            "pattern_description": result.pattern_description,
            "total_patterns_found": result.details.get("total_patterns_found", 0),
            "match_type": result.details.get("best_container", {}).get("match_type", "unknown"),
            "similarity_score": result.details.get("best_container", {}).get("similarity_score", 0),
        }
    except Exception as e:
        return {
            "folder": folder_path.name,
            "error": str(e)
        }


def analyze_from_db(object_id: int, content_signature: List[Dict], min_leaf_count: int = 3) -> Optional[Dict[str, Any]]:
    """DB에서 가져온 content_signature 분석"""
    try:
        result = evaluate_smartblock_eligibility(content_signature, min_leaf_count=min_leaf_count)

        return {
            "folder": str(object_id),
            "is_eligible": result.is_eligible,
            "score": result.score,
            "repeatable_count": result.repeatable_count,
            "repeatable_type": result.repeatable_type,
            "container_type": result.container_type,
            "pattern_description": result.pattern_description,
            "total_patterns_found": result.details.get("total_patterns_found", 0),
            "match_type": result.details.get("best_container", {}).get("match_type", "unknown"),
            "similarity_score": result.details.get("best_container", {}).get("similarity_score", 0),
        }
    except Exception as e:
        return {
            "folder": str(object_id),
            "error": str(e)
        }


def main():
    parser = argparse.ArgumentParser(description="스마트블록 적합성 판단 (룰베이스)")
    parser.add_argument("--dir", type=str, help="분석할 디렉토리 (폴더 기반)")
    parser.add_argument("--json", type=str, help="ID JSON 파일 (DB 기반) - valid_container_ids.json 형식")
    parser.add_argument("--folder", type=str, help="특정 폴더만 분석")
    parser.add_argument("--limit", type=int, help="분석할 개수 제한")
    parser.add_argument("--output", type=str, help="전체 결과 JSON 저장 파일")
    parser.add_argument("--output-dir", type=str, default="./data", help="CSV 결과 저장 디렉토리")
    parser.add_argument("--verbose", action="store_true", help="상세 출력")
    parser.add_argument("--save-csv", action="store_true", help="sm_valid.csv, sm_invalid.csv 저장")
    parser.add_argument("--min-leaf", type=int, default=3, help="최소 leaf 노드 수 (기본 3). 작은 라벨 그룹 필터링")
    args = parser.parse_args()

    # 전역 설정으로 min_leaf_count 전달
    global_min_leaf_count = args.min_leaf

    results = []

    # JSON 파일 기반 (DB에서 가져오기)
    if args.json:
        json_path = Path(args.json)
        if not json_path.exists():
            print(f"❌ JSON 파일을 찾을 수 없습니다: {json_path}")
            sys.exit(1)

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # valid_container_ids.json 형식 지원
        if isinstance(data, dict) and "ids" in data:
            object_ids = data["ids"]
        elif isinstance(data, list):
            object_ids = data
        else:
            print("❌ 지원하지 않는 JSON 형식입니다. {'ids': [...]} 또는 [...] 형식이어야 합니다.")
            sys.exit(1)

        if args.limit:
            object_ids = object_ids[:args.limit]

        print("=" * 70)
        print("스마트블록 적합성 판단 (룰베이스) - DB 모드")
        print("=" * 70)
        print(f"분석 대상: {len(object_ids)}개 ID")
        print(f"입력 파일: {json_path}")
        print(f"최소 leaf 노드 수: {global_min_leaf_count} (작은 라벨 그룹 필터링)")
        print("=" * 70)

        # DB에서 일괄 조회
        print(f"\n📥 DB에서 content_signature 일괄 조회 중...")
        content_signatures = fetch_content_signatures_batch(object_ids)
        print(f"  → {len(content_signatures)}개 조회 완료")

        # 분석
        print(f"\n🔍 분석 중...")
        for object_id in object_ids:
            if object_id not in content_signatures:
                results.append({
                    "folder": str(object_id),
                    "error": "content_signature 없음"
                })
                continue

            result = analyze_from_db(object_id, content_signatures[object_id], min_leaf_count=global_min_leaf_count)
            if result:
                results.append(result)

                if "error" in result:
                    print(f"  {result['folder']}: ERROR - {result['error']}")
                else:
                    status = "✓ 적합" if result["is_eligible"] else "✗ 부적합"
                    match_type = result.get("match_type", "?")
                    match_icon = {"exact": "=", "skeleton": "≈", "similar": "~"}.get(match_type, "?")
                    print(f"  {result['folder']}: {status} | {result['score']}/10 | "
                          f"{result['repeatable_count']}개 {result['repeatable_type']} [{match_icon}]")

                    if args.verbose and result.get("pattern_description"):
                        sim_score = result.get("similarity_score", 0)
                        print(f"    → {result['pattern_description']} (유사도: {sim_score:.0%})")

    # 폴더 기반 (기존 방식)
    else:
        target_dir = Path(args.dir) if args.dir else Path("./negative_samples")

        if args.folder:
            folders = [target_dir / args.folder]
        else:
            folders = sorted([f for f in target_dir.iterdir() if f.is_dir()])

        if args.limit:
            folders = folders[:args.limit]

        print("=" * 70)
        print("스마트블록 적합성 판단 (룰베이스) - 폴더 모드")
        print("=" * 70)
        print(f"분석 대상: {len(folders)}개 폴더")
        print(f"디렉토리: {target_dir}")
        print(f"최소 leaf 노드 수: {global_min_leaf_count} (작은 라벨 그룹 필터링)")
        print("=" * 70)

        for folder in folders:
            result = analyze_folder(folder, min_leaf_count=global_min_leaf_count)
            if result:
                results.append(result)

                if "error" in result:
                    print(f"  {result['folder']}: ERROR - {result['error']}")
                else:
                    status = "✓ 적합" if result["is_eligible"] else "✗ 부적합"
                    match_type = result.get("match_type", "?")
                    match_icon = {"exact": "=", "skeleton": "≈", "similar": "~"}.get(match_type, "?")
                    print(f"  {result['folder']}: {status} | {result['score']}/10 | "
                          f"{result['repeatable_count']}개 {result['repeatable_type']} [{match_icon}]")

                    if args.verbose and result.get("pattern_description"):
                        sim_score = result.get("similarity_score", 0)
                        print(f"    → {result['pattern_description']} (유사도: {sim_score:.0%})")

    # 통계
    print("\n" + "=" * 70)
    print("결과 요약")
    print("=" * 70)

    successful = [r for r in results if "error" not in r]
    eligible = [r for r in successful if r["is_eligible"]]

    print(f"총 분석: {len(successful)}개")
    print(f"적합: {len(eligible)}개 ({len(eligible)/len(successful)*100:.1f}%)")
    print(f"부적합: {len(successful) - len(eligible)}개")

    if successful:
        avg_score = sum(r["score"] for r in successful) / len(successful)
        print(f"평균 점수: {avg_score:.2f}/10")

        # 패턴 통계
        pattern_counter = Counter(r["repeatable_type"] for r in successful if r["repeatable_type"] != "없음")
        if pattern_counter:
            print("\n감지된 패턴:")
            for pattern, cnt in pattern_counter.most_common():
                print(f"  {pattern}: {cnt}개")

        # 매칭 타입 통계
        match_type_counter = Counter(r.get("match_type", "unknown") for r in successful)
        print("\n매칭 타입:")
        type_labels = {"exact": "완전 일치 [=]", "skeleton": "스켈레톤 일치 [≈]", "similar": "유사도 기반 [~]"}
        for mt, cnt in match_type_counter.most_common():
            label = type_labels.get(mt, mt)
            print(f"  {label}: {cnt}개")

    # 결과 저장
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n결과 저장: {output_path}")

    # CSV 저장 (sm_valid.csv, sm_invalid.csv)
    if args.save_csv:
        import csv
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        valid_results = [r for r in successful if r["is_eligible"]]
        invalid_results = [r for r in successful if not r["is_eligible"]]

        csv_columns = [
            "design_object_id", "score", "repeatable_count", "repeatable_type",
            "container_type", "match_type", "similarity_score", "pattern_description"
        ]

        # sm_valid.csv
        valid_path = output_dir / "sm_valid.csv"
        with open(valid_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(csv_columns)
            for r in valid_results:
                writer.writerow([
                    r["folder"],
                    r["score"],
                    r["repeatable_count"],
                    r["repeatable_type"],
                    r["container_type"],
                    r.get("match_type", ""),
                    f"{r.get('similarity_score', 0):.2f}",
                    r.get("pattern_description", "")
                ])
        print(f"\n적합 결과 저장: {valid_path} ({len(valid_results)}개)")

        # sm_invalid.csv
        invalid_path = output_dir / "sm_invalid.csv"
        with open(invalid_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(csv_columns)
            for r in invalid_results:
                writer.writerow([
                    r["folder"],
                    r["score"],
                    r["repeatable_count"],
                    r["repeatable_type"],
                    r["container_type"],
                    r.get("match_type", ""),
                    f"{r.get('similarity_score', 0):.2f}",
                    r.get("pattern_description", "")
                ])
        print(f"부적합 결과 저장: {invalid_path} ({len(invalid_results)}개)")


if __name__ == "__main__":
    main()
