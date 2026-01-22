#!/usr/bin/env python3
"""좌표 변환 테스트"""
import json
from pathlib import Path

# 샘플 파일 로드
sample_path = Path("negative_samples/277487")
original_file = sample_path / "structure_json.json"
fixed_file = sample_path / "structure_json_fixed.json"

with open(original_file) as f:
    original = json.load(f)

with open(fixed_file) as f:
    fixed = json.load(f)

def collect_positions(node, parent_abs_x=0, parent_abs_y=0, path=""):
    """노드의 위치 정보 수집 (상대좌표 → 절대좌표 계산)"""
    results = []
    pos = node.get('position', {})
    node_id = node.get('id', 'unknown')[:30]
    node_type = node.get('type', '')
    
    # 상대좌표
    rel_x = pos.get('x', 0)
    rel_y = pos.get('y', 0)
    
    # 절대좌표 계산
    abs_x = parent_abs_x + rel_x
    abs_y = parent_abs_y + rel_y
    
    current_path = f"{path}/{node_id}" if path else node_id
    results.append({
        "path": current_path,
        "type": node_type,
        "rel_x": rel_x,
        "rel_y": rel_y,
        "calc_abs_x": abs_x,
        "calc_abs_y": abs_y,
        "width": pos.get('width', 0),
        "height": pos.get('height', 0)
    })
    
    for child in node.get('children', []):
        results.extend(collect_positions(child, abs_x, abs_y, current_path))
    
    return results

print("=" * 80)
print("🔍 원본 파일 좌표 분석 (상대좌표)")
print("=" * 80)

original_positions = collect_positions(original)
for p in original_positions[:10]:
    print(f"  {p['type']:10} rel:({p['rel_x']:7.2f}, {p['rel_y']:7.2f}) → abs:({p['calc_abs_x']:7.2f}, {p['calc_abs_y']:7.2f})")

print("\n" + "=" * 80)
print("🔍 수정 파일 좌표 분석 (상대좌표)")
print("=" * 80)

fixed_positions = collect_positions(fixed)
for p in fixed_positions[:10]:
    print(f"  {p['type']:10} rel:({p['rel_x']:7.2f}, {p['rel_y']:7.2f}) → abs:({p['calc_abs_x']:7.2f}, {p['calc_abs_y']:7.2f})")

# 새로 생성된 Group 확인
print("\n" + "=" * 80)
print("🔍 새로 생성된 Group 확인")
print("=" * 80)

def find_groups(node, path=""):
    """새로 생성된 Group 찾기"""
    results = []
    node_id = node.get('id', '')
    node_type = node.get('type', '')
    
    current_path = f"{path}/{node_id[:20]}" if path else node_id[:20]
    
    if node_type == 'Group' and node_id.startswith(('group_', 'Group')) or len(node_id) == 36:  # UUID 형식
        pos = node.get('position', {})
        results.append({
            "path": current_path,
            "id": node_id[:30],
            "pos": f"({pos.get('x', 0):.2f}, {pos.get('y', 0):.2f})",
            "size": f"({pos.get('width', 0):.2f} x {pos.get('height', 0):.2f})",
            "children_count": len(node.get('children', []))
        })
        
        # 자식들의 좌표도 출력
        for child in node.get('children', []):
            child_pos = child.get('position', {})
            child_id = child.get('id', 'unknown')[:20]
            child_role = child.get('role', '').split('.')[-1]
            print(f"    └─ {child_role:15} pos:({child_pos.get('x', 0):7.2f}, {child_pos.get('y', 0):7.2f})")
    
    for child in node.get('children', []):
        results.extend(find_groups(child, current_path))
    
    return results

groups = find_groups(fixed)
for g in groups:
    print(f"  📦 Group: {g['id']}")
    print(f"     pos: {g['pos']}, size: {g['size']}, children: {g['children_count']}")

# 겹침 검증: 새 Group 내 자식들의 절대좌표가 올바른지
print("\n" + "=" * 80)
print("🔍 Group 내 자식들의 절대좌표 검증")
print("=" * 80)

def verify_group_children(node, parent_abs_x=0, parent_abs_y=0, path=""):
    """Group 내 자식들의 좌표 검증"""
    pos = node.get('position', {})
    node_id = node.get('id', 'unknown')[:30]
    node_type = node.get('type', '')
    
    rel_x = pos.get('x', 0)
    rel_y = pos.get('y', 0)
    abs_x = parent_abs_x + rel_x
    abs_y = parent_abs_y + rel_y
    
    current_path = f"{path}/{node_id}" if path else node_id
    
    # Group이면 자식들 검증
    if node_type == 'Group':
        print(f"\n  📦 {node_id}")
        print(f"     Group 절대좌표: ({abs_x:.2f}, {abs_y:.2f})")
        
        for child in node.get('children', []):
            child_pos = child.get('position', {})
            child_rel_x = child_pos.get('x', 0)
            child_rel_y = child_pos.get('y', 0)
            child_abs_x = abs_x + child_rel_x
            child_abs_y = abs_y + child_rel_y
            child_role = child.get('role', '').split('.')[-1]
            child_type = child.get('type', '')
            
            print(f"     └─ {child_role}({child_type})")
            print(f"        상대좌표: ({child_rel_x:.2f}, {child_rel_y:.2f})")
            print(f"        절대좌표: ({child_abs_x:.2f}, {child_abs_y:.2f})")
    
    for child in node.get('children', []):
        verify_group_children(child, abs_x, abs_y, current_path)

verify_group_children(fixed)
