#!/usr/bin/env python3
"""
negative_samples 폴더 내 structure_json_fixed.json 파일들에서
같은 컨테이너(Group/VStack/HStack/ZStack) 내에 겹치는 
Decoration, Marker, Frame, Image 개수 분석
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def get_role(node: Dict) -> str:
    role = node.get('role', '')
    return role.split('.')[-1] if '.' in role else role


def get_type(node: Dict) -> str:
    return node.get('type', '')


def get_bbox(node: Dict) -> Optional[Tuple[float, float, float, float]]:
    pos = node.get('position', {})
    if not pos:
        return None
    x, y = pos.get('x', 0), pos.get('y', 0)
    w, h = pos.get('width', 0), pos.get('height', 0)
    return (x, y, x + w, y + h)


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


def should_check_pair(node1: Dict, node2: Dict) -> bool:
    """
    process_design_object.py의 로직과 동일:
    - Background 제외
    - 컨테이너 타입 제외
    - Title, Description, Subtitle role 제외 (type이 Text여도 role이 Deco/Marker면 검사!)
    - Decoration + Decoration, Decoration + Marker, Marker + Marker만 검사
    """
    role1, role2 = get_role(node1), get_role(node2)
    type1, type2 = get_type(node1), get_type(node2)
    
    # Background는 겹침 허용
    if role1 == 'Background' or role2 == 'Background':
        return False
    
    # 컨테이너 타입 제외
    container_types = ['Group', 'HStack', 'VStack', 'ZStack', 'Grid']
    if type1 in container_types or type2 in container_types:
        return False
    
    # Title, Description, Subtitle role만 제외 (type 무관!)
    if role1 in ['Title', 'Description', 'Subtitle']:
        return False
    if role2 in ['Title', 'Description', 'Subtitle']:
        return False
    
    # ※ Frame/Image도 role이 Marker면 검사 대상! (제외 로직 삭제)
    
    # Decoration/Marker끼리 겹치면 검사
    if role1 == 'Decoration' and role2 == 'Decoration':
        return True
    if (role1 == 'Decoration' and role2 == 'Marker') or (role1 == 'Marker' and role2 == 'Decoration'):
        return True
    if role1 == 'Marker' and role2 == 'Marker':
        return True
    
    return False


def find_overlapping_pairs_in_container(children: List[Dict]) -> List[Tuple[int, int, Dict, Dict]]:
    """컨테이너 내에서 겹치는 쌍 찾기"""
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
                pairs.append((i, j, children[i], children[j]))
    return pairs


def analyze_node(node: Dict, parent_path: str = "") -> List[Dict]:
    """
    노드를 재귀적으로 분석하여 이슈 찾기
    반환: [{"container_id": ..., "container_type": ..., "overlapping_pairs": [...]}]
    """
    issues = []
    node_type = get_type(node)
    node_id = node.get('id', 'unknown')
    current_path = f"{parent_path}/{node_id}" if parent_path else node_id
    
    children = node.get('children', [])
    
    # 컨테이너인 경우 자식들 간 겹침 검사
    if node_type in ['Group', 'HStack', 'VStack', 'ZStack', 'Grid'] and children:
        overlapping = find_overlapping_pairs_in_container(children)
        if overlapping:
            issue_details = []
            for i, j, node1, node2 in overlapping:
                issue_details.append({
                    "idx": (i, j),
                    "node1": {
                        "id": node1.get('id', 'unknown')[:30],
                        "role": get_role(node1),
                        "type": get_type(node1)
                    },
                    "node2": {
                        "id": node2.get('id', 'unknown')[:30],
                        "role": get_role(node2),
                        "type": get_type(node2)
                    }
                })
            issues.append({
                "container_id": node_id,
                "container_type": node_type,
                "path": current_path,
                "overlapping_pairs": issue_details
            })
    
    # 자식 노드들도 재귀적으로 분석
    for child in children:
        issues.extend(analyze_node(child, current_path))
    
    return issues


def analyze_file(file_path: Path) -> Dict:
    """단일 파일 분석"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            structure = json.load(f)
        
        issues = analyze_node(structure)
        return {
            "file": str(file_path),
            "sample_id": file_path.parent.name,
            "has_issues": len(issues) > 0,
            "issue_count": len(issues),
            "total_overlapping_pairs": sum(len(issue['overlapping_pairs']) for issue in issues),
            "issues": issues
        }
    except Exception as e:
        return {
            "file": str(file_path),
            "sample_id": file_path.parent.name,
            "error": str(e)
        }


def main():
    negative_samples_dir = Path(__file__).parent / "negative_samples"
    
    if not negative_samples_dir.exists():
        print(f"❌ 디렉토리를 찾을 수 없습니다: {negative_samples_dir}")
        return
    
    # 모든 structure_json_fixed.json 파일 찾기
    files = list(negative_samples_dir.glob("*/structure_json_fixed.json"))
    files.sort(key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0)
    
    print(f"📁 분석 대상: {len(files)}개 파일")
    print("=" * 70)
    
    results = []
    total_issues = 0
    total_pairs = 0
    files_with_issues = 0
    
    for file_path in files:
        result = analyze_file(file_path)
        results.append(result)
        
        if result.get('has_issues'):
            files_with_issues += 1
            total_issues += result['issue_count']
            total_pairs += result['total_overlapping_pairs']
            
            print(f"\n🔴 {result['sample_id']}: {result['issue_count']}개 컨테이너에서 {result['total_overlapping_pairs']}쌍 겹침")
            for issue in result['issues']:
                print(f"   📦 {issue['container_type']} ({issue['container_id'][:30]})")
                for pair in issue['overlapping_pairs']:
                    n1, n2 = pair['node1'], pair['node2']
                    print(f"      ↔️  {n1['role']}({n1['type']}) <-> {n2['role']}({n2['type']})")
    
    # 요약
    print("\n" + "=" * 70)
    print("📊 요약")
    print("=" * 70)
    print(f"   총 파일 수: {len(files)}")
    print(f"   이슈 있는 파일: {files_with_issues}")
    print(f"   이슈 없는 파일: {len(files) - files_with_issues}")
    print(f"   총 이슈 컨테이너: {total_issues}")
    print(f"   총 겹침 쌍: {total_pairs}")
    
    # 결과 저장
    output_file = Path(__file__).parent / "data" / "overlap_analysis_result.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "total_files": len(files),
                "files_with_issues": files_with_issues,
                "total_issue_containers": total_issues,
                "total_overlapping_pairs": total_pairs
            },
            "results": results
        }, f, ensure_ascii=False, indent=2)
    print(f"\n💾 상세 결과 저장: {output_file}")


if __name__ == "__main__":
    main()
