#!/usr/bin/env python3
"""
JSON 파일에서 중복 제외한 고유 layout_id 개수 파악
"""

import json
import argparse
from collections import Counter


def count_unique_layouts(json_path: str):
    """JSON 파일에서 고유 layout_id 개수 계산"""
    
    print(f"📂 파일 로드 중: {json_path}")
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 'objects' 또는 'candidates' 키 자동 감지
    objects = data.get('objects') or data.get('candidates') or []
    total_count = len(objects)
    
    print(f"📊 전체 objects 개수: {total_count:,}개")
    
    # layout_id 수집
    layout_ids = [obj.get('layout_id') for obj in objects if obj.get('layout_id') is not None]
    
    # 고유 layout_id
    unique_layout_ids = set(layout_ids)
    unique_count = len(unique_layout_ids)
    
    # layout_id별 개수 (상위 10개)
    layout_counter = Counter(layout_ids)
    
    print(f"\n{'='*50}")
    print(f"📋 결과")
    print(f"{'='*50}")
    print(f"  - 전체 objects: {total_count:,}개")
    print(f"  - 고유 layout_id: {unique_count:,}개")
    print(f"  - 평균 objects/layout: {total_count/unique_count:.1f}개" if unique_count > 0 else "")
    
    # layout_id가 가장 많이 나온 상위 10개
    print(f"\n📊 layout_id별 objects 개수 (상위 10개):")
    for layout_id, count in layout_counter.most_common(10):
        print(f"  - layout_id {layout_id}: {count}개")
    
    # rlsc_id도 확인
    rlsc_ids = [obj.get('rlsc_id') for obj in objects if obj.get('rlsc_id') is not None]
    unique_rlsc_ids = set(rlsc_ids)
    
    print(f"\n📊 추가 통계:")
    print(f"  - 고유 rlsc_id: {len(unique_rlsc_ids):,}개")
    
    return {
        'total_objects': total_count,
        'unique_layout_ids': unique_count,
        'unique_rlsc_ids': len(unique_rlsc_ids),
        'layout_id_distribution': dict(layout_counter.most_common(20))
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='고유 layout_id 개수 파악')
    parser.add_argument('--input', type=str, default='data/my_output.json',
                        help='입력 JSON 파일 경로')
    args = parser.parse_args()
    
    result = count_unique_layouts(args.input)
