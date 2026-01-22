#!/usr/bin/env python3
"""
ID별 depth 및 통계 정보를 분석합니다.
"""

import json
import csv
from pathlib import Path
from collections import defaultdict, Counter


def load_candidates_with_stats(file_path):
    """JSON 파일에서 candidates의 상세 정보를 추출합니다."""
    print(f"Loading candidates from {file_path}...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    candidates_dict = {}
    if 'candidates' in data:
        for candidate in data['candidates']:
            if 'id' in candidate:
                id_value = candidate['id']
                candidates_dict[id_value] = {
                    'id': id_value,
                    'max_depth': candidate.get('analysis', {}).get('max_depth', 0),
                    'issue_count': candidate.get('analysis', {}).get('issue_count', 0),
                    'issue_types': candidate.get('analysis', {}).get('issue_types', []),
                    'layout_id': candidate.get('layout_id'),
                    'rlsc_id': candidate.get('rlsc_id'),
                    'element_count': candidate.get('design_object_meta', {}).get('structure', {}).get('element_count', 0),
                    'max_structure_depth': candidate.get('design_object_meta', {}).get('structure', {}).get('max_depth', 0),
                }
    
    print(f"  - Loaded {len(candidates_dict)} candidates")
    return candidates_dict


def categorize_ids(candidates_ratio, candidates_0):
    """ID를 세 그룹으로 분류합니다."""
    ids_ratio = set(candidates_ratio.keys())
    ids_0 = set(candidates_0.keys())
    
    both = ids_ratio & ids_0
    ratio_only = ids_ratio - ids_0
    zero_only = ids_0 - ids_ratio
    
    return {
        'both': both,
        'ratio_only': ratio_only,
        'zero_only': zero_only
    }


def calculate_statistics(ids, candidates_dict, category_name):
    """특정 ID 그룹의 통계를 계산합니다."""
    print(f"\n=== {category_name} 통계 ({len(ids)}개 ID) ===")
    
    if not ids:
        print("  - 데이터 없음")
        return None
    
    depths = []
    issue_counts = []
    issue_types_counter = Counter()
    element_counts = []
    structure_depths = []
    
    for id_value in ids:
        if id_value in candidates_dict:
            candidate = candidates_dict[id_value]
            depths.append(candidate['max_depth'])
            issue_counts.append(candidate['issue_count'])
            element_counts.append(candidate['element_count'])
            structure_depths.append(candidate['max_structure_depth'])
            
            for issue_type in candidate['issue_types']:
                issue_types_counter[issue_type] += 1
    
    # 통계 계산
    stats = {
        'count': len(ids),
        'depth': {
            'min': min(depths) if depths else 0,
            'max': max(depths) if depths else 0,
            'avg': sum(depths) / len(depths) if depths else 0,
            'distribution': Counter(depths)
        },
        'issue_count': {
            'min': min(issue_counts) if issue_counts else 0,
            'max': max(issue_counts) if issue_counts else 0,
            'avg': sum(issue_counts) / len(issue_counts) if issue_counts else 0,
            'distribution': Counter(issue_counts)
        },
        'element_count': {
            'min': min(element_counts) if element_counts else 0,
            'max': max(element_counts) if element_counts else 0,
            'avg': sum(element_counts) / len(element_counts) if element_counts else 0,
        },
        'structure_depth': {
            'min': min(structure_depths) if structure_depths else 0,
            'max': max(structure_depths) if structure_depths else 0,
            'avg': sum(structure_depths) / len(structure_depths) if structure_depths else 0,
            'distribution': Counter(structure_depths)
        },
        'issue_types': dict(issue_types_counter)
    }
    
    # 출력
    print(f"  [Depth (문제 최대 깊이)]")
    print(f"    - 범위: {stats['depth']['min']} ~ {stats['depth']['max']}")
    print(f"    - 평균: {stats['depth']['avg']:.2f}")
    print(f"    - 분포: {dict(sorted(stats['depth']['distribution'].items()))}")
    
    print(f"  [Issue Count (이슈 개수)]")
    print(f"    - 범위: {stats['issue_count']['min']} ~ {stats['issue_count']['max']}")
    print(f"    - 평균: {stats['issue_count']['avg']:.2f}")
    print(f"    - 분포: {dict(sorted(stats['issue_count']['distribution'].items()))}")
    
    print(f"  [Element Count (요소 개수)]")
    print(f"    - 범위: {stats['element_count']['min']} ~ {stats['element_count']['max']}")
    print(f"    - 평균: {stats['element_count']['avg']:.2f}")
    
    print(f"  [Structure Depth (구조 최대 깊이)]")
    print(f"    - 범위: {stats['structure_depth']['min']} ~ {stats['structure_depth']['max']}")
    print(f"    - 평균: {stats['structure_depth']['avg']:.2f}")
    print(f"    - 분포: {dict(sorted(stats['structure_depth']['distribution'].items()))}")
    
    print(f"  [Issue Types (이슈 타입)]")
    for issue_type, count in sorted(stats['issue_types'].items(), key=lambda x: -x[1]):
        print(f"    - {issue_type}: {count}개 ({count/len(ids)*100:.1f}%)")
    
    return stats


def save_detailed_csv(ids_by_category, candidates_ratio, candidates_0, output_path):
    """각 ID의 상세 정보를 CSV로 저장합니다."""
    print(f"\n상세 CSV 생성 중: {output_path}")
    
    all_ids = ids_by_category['both'] | ids_by_category['ratio_only'] | ids_by_category['zero_only']
    
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        
        # 헤더
        writer.writerow([
            'id', 'category', 
            'in_ratio', 'in_0',
            'max_depth', 'issue_count', 'issue_types',
            'element_count', 'structure_depth',
            'layout_id', 'rlsc_id'
        ])
        
        # 데이터
        for id_value in sorted(all_ids):
            # 카테고리 결정
            if id_value in ids_by_category['both']:
                category = '둘 다 포함'
                in_ratio = 'O'
                in_0 = 'O'
                candidate = candidates_ratio.get(id_value, candidates_0.get(id_value))
            elif id_value in ids_by_category['ratio_only']:
                category = 'ratio만 포함'
                in_ratio = 'O'
                in_0 = 'X'
                candidate = candidates_ratio.get(id_value)
            else:
                category = '0만 포함'
                in_ratio = 'X'
                in_0 = 'O'
                candidate = candidates_0.get(id_value)
            
            if candidate:
                writer.writerow([
                    id_value,
                    category,
                    in_ratio,
                    in_0,
                    candidate['max_depth'],
                    candidate['issue_count'],
                    ', '.join(candidate['issue_types']),
                    candidate['element_count'],
                    candidate['max_structure_depth'],
                    candidate['layout_id'],
                    candidate['rlsc_id']
                ])
    
    print(f"✓ 상세 CSV 저장 완료!")


def save_summary_csv(stats_by_category, output_path):
    """통계 요약을 CSV로 저장합니다."""
    print(f"\n통계 요약 CSV 생성 중: {output_path}")
    
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        
        # 헤더
        writer.writerow([
            'category', 'count',
            'depth_min', 'depth_max', 'depth_avg',
            'issue_count_min', 'issue_count_max', 'issue_count_avg',
            'element_count_min', 'element_count_max', 'element_count_avg',
            'structure_depth_min', 'structure_depth_max', 'structure_depth_avg',
            'top_issue_type', 'top_issue_count'
        ])
        
        # 데이터
        for category_name, stats in stats_by_category.items():
            if stats:
                top_issue = max(stats['issue_types'].items(), key=lambda x: x[1]) if stats['issue_types'] else ('없음', 0)
                
                writer.writerow([
                    category_name,
                    stats['count'],
                    stats['depth']['min'],
                    stats['depth']['max'],
                    f"{stats['depth']['avg']:.2f}",
                    stats['issue_count']['min'],
                    stats['issue_count']['max'],
                    f"{stats['issue_count']['avg']:.2f}",
                    stats['element_count']['min'],
                    stats['element_count']['max'],
                    f"{stats['element_count']['avg']:.2f}",
                    stats['structure_depth']['min'],
                    stats['structure_depth']['max'],
                    f"{stats['structure_depth']['avg']:.2f}",
                    top_issue[0],
                    top_issue[1]
                ])
    
    print(f"✓ 통계 요약 CSV 저장 완료!")


def main():
    # 파일 경로 설정
    data_dir = Path(__file__).parent / 'data'
    file1 = data_dir / 'fix_candidates_ratio.json'
    file2 = data_dir / 'fix_candidates_0.json'
    output_detailed_csv = data_dir / 'id_statistics_detailed.csv'
    output_summary_csv = data_dir / 'id_statistics_summary.csv'
    
    # 데이터 로드
    candidates_ratio = load_candidates_with_stats(file1)
    candidates_0 = load_candidates_with_stats(file2)
    
    # ID 분류
    ids_by_category = categorize_ids(candidates_ratio, candidates_0)
    
    print(f"\n=== ID 분류 결과 ===")
    print(f"둘 다 포함: {len(ids_by_category['both'])} IDs")
    print(f"ratio만 포함: {len(ids_by_category['ratio_only'])} IDs")
    print(f"0만 포함: {len(ids_by_category['zero_only'])} IDs")
    
    # 각 카테고리별 통계 계산
    stats_by_category = {}
    
    # 둘 다 포함
    stats_by_category['둘 다 포함'] = calculate_statistics(
        ids_by_category['both'], 
        candidates_ratio,  # ratio 파일 기준
        "둘 다 포함"
    )
    
    # ratio만 포함
    stats_by_category['ratio만 포함'] = calculate_statistics(
        ids_by_category['ratio_only'], 
        candidates_ratio,
        "ratio만 포함"
    )
    
    # 0만 포함
    stats_by_category['0만 포함'] = calculate_statistics(
        ids_by_category['zero_only'], 
        candidates_0,
        "0만 포함"
    )
    
    # CSV 저장
    save_detailed_csv(ids_by_category, candidates_ratio, candidates_0, output_detailed_csv)
    save_summary_csv(stats_by_category, output_summary_csv)
    
    print(f"\n✅ 분석 완료!")
    print(f"상세 결과: {output_detailed_csv}")
    print(f"통계 요약: {output_summary_csv}")


if __name__ == '__main__':
    main()
