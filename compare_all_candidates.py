#!/usr/bin/env python3
"""
세 개의 fix_candidates JSON 파일을 비교하여 ID 포함 여부를 분석합니다.
- fix_candidates_0.json (절대 기준)
- fix_candidates_ratio.json (비율 10% 기준)
- fix_candidates_ratio_20.json (비율 20% 기준)
"""

import json
import csv
from pathlib import Path
from collections import Counter


def load_candidates_with_stats(file_path):
    """JSON 파일에서 candidates의 상세 정보를 추출합니다."""
    print(f"Loading candidates from {file_path.name}...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 메타데이터 출력
    if 'metadata' in data:
        meta = data['metadata']
        print(f"  - Config: {meta.get('overlap_config', {})}")
        print(f"  - Needs fix count: {meta.get('statistics', {}).get('needs_fix_count', 'N/A')}")
    
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
                    'element_count': candidate.get('design_object_meta', {}).get('structure', {}).get('element_count', 0),
                    'max_structure_depth': candidate.get('design_object_meta', {}).get('structure', {}).get('max_depth', 0),
                }
    
    print(f"  - Loaded {len(candidates_dict)} candidates")
    return candidates_dict


def calculate_statistics(ids, candidates_dict, category_name):
    """특정 ID 그룹의 통계를 계산합니다."""
    print(f"\n=== {category_name} ({len(ids)}개 ID) ===")
    
    if not ids:
        print("  - 데이터 없음")
        return None
    
    depths = []
    issue_counts = []
    issue_types_counter = Counter()
    
    for id_value in ids:
        if id_value in candidates_dict:
            candidate = candidates_dict[id_value]
            depths.append(candidate['max_depth'])
            issue_counts.append(candidate['issue_count'])
            
            for issue_type in candidate['issue_types']:
                issue_types_counter[issue_type] += 1
    
    # 통계 출력
    print(f"  [Depth] 범위: {min(depths) if depths else 0} ~ {max(depths) if depths else 0}, 평균: {sum(depths)/len(depths) if depths else 0:.2f}")
    print(f"  [Issue Count] 범위: {min(issue_counts) if issue_counts else 0} ~ {max(issue_counts) if issue_counts else 0}, 평균: {sum(issue_counts)/len(issue_counts) if issue_counts else 0:.2f}")
    print(f"  [Issue Types]")
    for issue_type, count in sorted(issue_types_counter.items(), key=lambda x: -x[1]):
        print(f"    - {issue_type}: {count}개 ({count/len(ids)*100:.1f}%)")
    
    return {
        'count': len(ids),
        'depths': depths,
        'issue_counts': issue_counts,
        'issue_types': dict(issue_types_counter)
    }


def main():
    # 파일 경로 설정
    data_dir = Path(__file__).parent / 'data'
    file_0 = data_dir / 'fix_candidates_0.json'
    file_ratio_10 = data_dir / 'fix_candidates_ratio.json'
    file_ratio_20 = data_dir / 'fix_candidates_ratio_20.json'
    
    # 데이터 로드
    print("=" * 60)
    candidates_0 = load_candidates_with_stats(file_0)
    print()
    candidates_ratio_10 = load_candidates_with_stats(file_ratio_10)
    print()
    candidates_ratio_20 = load_candidates_with_stats(file_ratio_20)
    print("=" * 60)
    
    # ID 집합
    ids_0 = set(candidates_0.keys())
    ids_ratio_10 = set(candidates_ratio_10.keys())
    ids_ratio_20 = set(candidates_ratio_20.keys())
    
    print(f"\n{'='*60}")
    print("📊 ID 수 비교")
    print(f"{'='*60}")
    print(f"fix_candidates_0.json (절대 기준):     {len(ids_0):,}개")
    print(f"fix_candidates_ratio.json (비율 10%):  {len(ids_ratio_10):,}개")
    print(f"fix_candidates_ratio_20.json (비율 20%): {len(ids_ratio_20):,}개")
    
    print(f"\n{'='*60}")
    print("🔗 집합 관계 분석")
    print(f"{'='*60}")
    
    # 교집합 분석
    all_three = ids_0 & ids_ratio_10 & ids_ratio_20
    print(f"세 파일 모두 포함:                     {len(all_three):,}개")
    
    only_0 = ids_0 - ids_ratio_10 - ids_ratio_20
    print(f"0만 포함 (10%, 20% 모두 없음):        {len(only_0):,}개")
    
    only_ratio_10 = ids_ratio_10 - ids_0
    print(f"ratio_10만 포함 (0에 없음):            {len(only_ratio_10):,}개")
    
    only_ratio_20 = ids_ratio_20 - ids_0
    print(f"ratio_20만 포함 (0에 없음):            {len(only_ratio_20):,}개")
    
    in_0_and_10_not_20 = (ids_0 & ids_ratio_10) - ids_ratio_20
    print(f"0과 10%에만 포함 (20%에 없음):        {len(in_0_and_10_not_20):,}개")
    
    in_0_not_10_not_20 = ids_0 - ids_ratio_10
    print(f"0에만 포함 (10%에 없음):              {len(in_0_not_10_not_20):,}개")
    
    in_10_not_20 = ids_ratio_10 - ids_ratio_20
    print(f"10%에는 있지만 20%에 없음:            {len(in_10_not_20):,}개")
    
    print(f"\n{'='*60}")
    print("📈 포함 관계 검증")
    print(f"{'='*60}")
    print(f"ratio_20 ⊆ ratio_10: {ids_ratio_20.issubset(ids_ratio_10)}")
    print(f"ratio_10 ⊆ 0: {ids_ratio_10.issubset(ids_0)}")
    print(f"ratio_20 ⊆ 0: {ids_ratio_20.issubset(ids_0)}")
    
    # 각 그룹별 통계
    print(f"\n{'='*60}")
    print("📊 그룹별 상세 통계")
    print(f"{'='*60}")
    
    # 세 파일 모두에 있는 것
    calculate_statistics(all_three, candidates_0, "세 파일 모두 포함")
    
    # 0과 10%에만 있고 20%에 없는 것
    calculate_statistics(in_0_and_10_not_20, candidates_0, "0과 10%에만 포함 (20%에 없음)")
    
    # 0에만 있는 것
    calculate_statistics(only_0, candidates_0, "0에만 포함 (10%, 20% 모두 없음)")
    
    # CSV 저장
    output_csv = data_dir / 'id_comparison_all_three.csv'
    print(f"\n📝 CSV 저장 중: {output_csv}")
    
    all_ids = ids_0 | ids_ratio_10 | ids_ratio_20
    
    with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['id', 'in_0', 'in_ratio_10', 'in_ratio_20', 'category'])
        
        for id_value in sorted(all_ids):
            in_0 = 'O' if id_value in ids_0 else 'X'
            in_10 = 'O' if id_value in ids_ratio_10 else 'X'
            in_20 = 'O' if id_value in ids_ratio_20 else 'X'
            
            if in_0 == 'O' and in_10 == 'O' and in_20 == 'O':
                category = '세 파일 모두'
            elif in_0 == 'O' and in_10 == 'O' and in_20 == 'X':
                category = '0과 10%만'
            elif in_0 == 'O' and in_10 == 'X':
                category = '0만'
            else:
                category = '기타'
            
            writer.writerow([id_value, in_0, in_10, in_20, category])
    
    print(f"✅ 완료!")
    print(f"\n결과 파일: {output_csv}")


if __name__ == '__main__':
    main()
