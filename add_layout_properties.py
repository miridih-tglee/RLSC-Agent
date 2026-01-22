#!/usr/bin/env python3
"""
structure_json에 padding, gap, direction을 룰베이스로 계산하여 추가하는 스크립트

사용법:
    아래 설정 변수를 수정한 후 실행
    python add_layout_properties.py

룰:
- direction: HStack → "horizontal", VStack → "vertical"
- padding: 부모 영역과 자식들의 위치 차이로 계산
- gap: 자식들 사이의 간격 (평균값)
"""

import json
from pathlib import Path

# ============================================================
# 🔧 설정 변수 (여기서 수정하세요)
# ============================================================

# 입력 파일 경로
INPUT_FILE = "/Users/miridih/Desktop/tg/data/redesigned_output.json"

# 출력 파일 경로 (None이면 입력파일_with_layout.json으로 자동 생성)
OUTPUT_FILE = "/Users/miridih/Desktop/tg/data/277987/structure_json_with_layout.json"  # 예: "data/302612/output.json"

# ============================================================


def calculate_layout_properties(node: dict) -> dict:
    """노드에 padding, gap, direction 속성을 계산하여 추가"""
    
    node_type = node.get("type", "")
    children = node.get("children", [])
    position = node.get("position", {})
    
    # Stack 타입인 경우에만 처리
    if node_type in ("HStack", "VStack"):
        # direction 설정
        direction = "horizontal" if node_type == "HStack" else "vertical"
        node["direction"] = direction
        
        if children and position:
            parent_width = position.get("width", 0)
            parent_height = position.get("height", 0)
            
            # 자식들의 위치 정보 수집
            child_positions = []
            for child in children:
                child_pos = child.get("position", {})
                if child_pos:
                    child_positions.append({
                        "x": child_pos.get("x", 0),
                        "y": child_pos.get("y", 0),
                        "width": child_pos.get("width", 0),
                        "height": child_pos.get("height", 0)
                    })
            
            if child_positions:
                # Padding 계산
                min_x = min(cp["x"] for cp in child_positions)
                min_y = min(cp["y"] for cp in child_positions)
                max_right = max(cp["x"] + cp["width"] for cp in child_positions)
                max_bottom = max(cp["y"] + cp["height"] for cp in child_positions)
                
                padding = {
                    "top": round(min_y, 2),
                    "bottom": round(max(0, parent_height - max_bottom), 2),
                    "left": round(min_x, 2),
                    "right": round(max(0, parent_width - max_right), 2)
                }
                node["padding"] = padding
                
                # Gap 계산 (자식이 2개 이상일 때만)
                if len(child_positions) >= 2:
                    gaps = []
                    
                    if direction == "horizontal":
                        # x 기준 정렬하여 gap 계산
                        sorted_children = sorted(child_positions, key=lambda c: c["x"])
                        for i in range(len(sorted_children) - 1):
                            curr = sorted_children[i]
                            next_ = sorted_children[i + 1]
                            gap = next_["x"] - (curr["x"] + curr["width"])
                            gaps.append(gap)
                    else:  # vertical
                        # y 기준 정렬하여 gap 계산
                        sorted_children = sorted(child_positions, key=lambda c: c["y"])
                        for i in range(len(sorted_children) - 1):
                            curr = sorted_children[i]
                            next_ = sorted_children[i + 1]
                            gap = next_["y"] - (curr["y"] + curr["height"])
                            gaps.append(gap)
                    
                    if gaps:
                        # 평균 gap 계산 (음수는 0으로 처리)
                        avg_gap = sum(max(0, g) for g in gaps) / len(gaps)
                        node["gap"] = round(avg_gap, 2)
                    else:
                        node["gap"] = 0
                else:
                    node["gap"] = 0
            else:
                # 자식 위치 정보가 없으면 기본값
                node["padding"] = {"top": 0, "bottom": 0, "left": 0, "right": 0}
                node["gap"] = 0
        else:
            # 자식이 없거나 position이 없으면 기본값
            node["padding"] = {"top": 0, "bottom": 0, "left": 0, "right": 0}
            node["gap"] = 0
    
    # 자식 노드들도 재귀적으로 처리
    if children:
        for child in children:
            calculate_layout_properties(child)
    
    return node


def process_structure_json(input_path: Path, output_path: Path) -> None:
    """structure_json 파일을 처리하여 저장"""
    
    print(f"📥 입력 파일: {input_path}")
    
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 레이아웃 속성 계산
    result = calculate_layout_properties(data)
    
    # 결과 저장
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 출력 파일: {output_path}")
    
    # 통계 출력
    stats = count_layout_nodes(result)
    print(f"\n📊 처리 결과:")
    print(f"   - HStack 노드: {stats['hstack']}개")
    print(f"   - VStack 노드: {stats['vstack']}개")
    print(f"   - 총 처리: {stats['total']}개")


def count_layout_nodes(node: dict, stats: dict = None) -> dict:
    """레이아웃 노드 개수 카운트"""
    if stats is None:
        stats = {"hstack": 0, "vstack": 0, "total": 0}
    
    node_type = node.get("type", "")
    if node_type == "HStack":
        stats["hstack"] += 1
        stats["total"] += 1
    elif node_type == "VStack":
        stats["vstack"] += 1
        stats["total"] += 1
    
    for child in node.get("children", []):
        count_layout_nodes(child, stats)
    
    return stats


def main():
    # 스크립트 위치 기준 경로 설정
    base_path = Path(__file__).parent
    
    input_path = base_path / INPUT_FILE
    
    if not input_path.exists():
        print(f"❌ 파일을 찾을 수 없습니다: {input_path}")
        return
    
    # 출력 파일 경로 설정
    if OUTPUT_FILE:
        output_path = base_path / OUTPUT_FILE
    else:
        # 기본: 같은 폴더에 _with_layout.json 접미사로 저장
        output_path = input_path.parent / f"{input_path.stem}_with_layout.json"
    
    process_structure_json(input_path, output_path)


if __name__ == "__main__":
    main()
