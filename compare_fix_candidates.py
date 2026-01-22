#!/usr/bin/env python3
"""
두 개의 fix_candidates JSON 파일을 비교하여 ID 포함 여부를 분석하고 CSV로 저장합니다.
"""

import json
import csv
from pathlib import Path


def load_ids_from_json(file_path):
    """JSON 파일에서 candidates의 id 목록을 추출합니다."""
    print(f"Loading IDs from {file_path}...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    ids = set()
    if 'candidates' in data:
        for candidate in data['candidates']:
            if 'id' in candidate:
                ids.add(candidate['id'])
    
    print(f"  - Loaded {len(ids)} IDs")
    return ids


def compare_and_save_to_csv(file1_path, file2_path, output_csv_path):
    """
    두 JSON 파일의 ID를 비교하고 포함 여부를 CSV로 저장합니다.
    
    Args:
        file1_path: fix_candidates_ratio.json 경로
        file2_path: fix_candidates_0.json 경로
        output_csv_path: 출력 CSV 파일 경로
    """
    # ID 로드
    ids_ratio = load_ids_from_json(file1_path)
    ids_0 = load_ids_from_json(file2_path)
    
    # 전체 ID 집합 (합집합)
    all_ids = ids_ratio | ids_0
    
    # 통계 출력
    print(f"\n=== 분석 결과 ===")
    print(f"fix_candidates_ratio.json: {len(ids_ratio)} IDs")
    print(f"fix_candidates_0.json: {len(ids_0)} IDs")
    print(f"전체 고유 ID: {len(all_ids)} IDs")
    print(f"교집합 (둘 다 포함): {len(ids_ratio & ids_0)} IDs")
    print(f"ratio에만 있는 ID: {len(ids_ratio - ids_0)} IDs")
    print(f"0에만 있는 ID: {len(ids_0 - ids_ratio)} IDs")
    
    # CSV 작성
    print(f"\nCSV 파일 생성 중: {output_csv_path}")
    
    with open(output_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        
        # 헤더 작성
        writer.writerow(['id', 'in_fix_candidates_ratio', 'in_fix_candidates_0', 'status'])
        
        # ID를 정렬하여 작성
        for id_value in sorted(all_ids):
            in_ratio = 'O' if id_value in ids_ratio else 'X'
            in_0 = 'O' if id_value in ids_0 else 'X'
            
            # 상태 결정
            if in_ratio == 'O' and in_0 == 'O':
                status = '둘 다 포함'
            elif in_ratio == 'O':
                status = 'ratio만 포함'
            else:
                status = '0만 포함'
            
            writer.writerow([id_value, in_ratio, in_0, status])
    
    print(f"✓ CSV 파일 저장 완료!")
    
    return {
        'total': len(all_ids),
        'both': len(ids_ratio & ids_0),
        'ratio_only': len(ids_ratio - ids_0),
        '0_only': len(ids_0 - ids_ratio)
    }


def main():
    # 파일 경로 설정
    data_dir = Path(__file__).parent / 'data'
    file1 = data_dir / 'fix_candidates_ratio.json'
    file2 = data_dir / 'fix_candidates_0.json'
    output_csv = data_dir / 'id_comparison_result.csv'
    
    # 파일 존재 확인
    if not file1.exists():
        print(f"❌ 파일을 찾을 수 없습니다: {file1}")
        return
    
    if not file2.exists():
        print(f"❌ 파일을 찾을 수 없습니다: {file2}")
        return
    
    # 비교 및 저장
    stats = compare_and_save_to_csv(file1, file2, output_csv)
    
    print(f"\n=== 최종 통계 ===")
    print(f"총 ID 수: {stats['total']}")
    print(f"둘 다 포함: {stats['both']} ({stats['both']/stats['total']*100:.1f}%)")
    print(f"ratio만 포함: {stats['ratio_only']} ({stats['ratio_only']/stats['total']*100:.1f}%)")
    print(f"0만 포함: {stats['0_only']} ({stats['0_only']/stats['total']*100:.1f}%)")
    print(f"\n결과 파일: {output_csv}")


if __name__ == '__main__':
    main()
