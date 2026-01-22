#!/usr/bin/env python3
"""
structure_json_fixed를 DB에 업데이트하는 스크립트

폴더 내의 structure_json_fixed.json 파일을 읽어서
design_objects 테이블의 structure_json_fixed 컬럼에 업데이트합니다.

사용법:
    # 단일 ID 폴더
    python update_structure_json_fixed.py 283782
    
    # 여러 ID
    python update_structure_json_fixed.py 283782,283725,277457
    
    # 특정 디렉토리의 모든 폴더
    python update_structure_json_fixed.py --dir /path/to/data
    
    # 기본 data 폴더 사용
    python update_structure_json_fixed.py --dir ./data
    
    # dry-run (실제 업데이트 없이 확인만)
    python update_structure_json_fixed.py --dir ./data --dry-run
"""

import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Optional

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2가 설치되어 있지 않습니다. 설치해주세요:")
    print("  pip install psycopg2-binary")
    sys.exit(1)


# ============================================================
# DB 설정
# ============================================================
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54322,
    "user": "postgres",
    "password": "postgres",
    "dbname": "postgres"
}


# ============================================================
# DB 함수
# ============================================================
def update_structure_json_fixed(object_id: int, structure_json_fixed: Dict, dry_run: bool = False) -> bool:
    """
    DB의 structure_json_fixed 컬럼 업데이트
    
    Args:
        object_id: 디자인 오브젝트 ID
        structure_json_fixed: 수정된 structure JSON
        dry_run: True면 실제 업데이트 없이 확인만
    
    Returns:
        성공 여부
    """
    if dry_run:
        print(f"    [DRY-RUN] ID={object_id} 업데이트 예정")
        return True
    
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            query = """
                UPDATE design_objects
                SET structure_json_fixed = %s
                WHERE id = %s
            """
            cur.execute(query, (json.dumps(structure_json_fixed, ensure_ascii=False), object_id))
            
            if cur.rowcount == 0:
                print(f"    ⚠️ ID={object_id}에 해당하는 레코드를 찾을 수 없습니다.")
                return False
            
            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        print(f"    ❌ DB 오류: {e}")
        return False
    finally:
        conn.close()


def check_column_exists() -> bool:
    """structure_json_fixed 컬럼 존재 여부 확인"""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            query = """
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'design_objects' 
                AND column_name = 'structure_json_fixed'
            """
            cur.execute(query)
            result = cur.fetchone()
            return result is not None
    finally:
        conn.close()


def create_column_if_not_exists() -> bool:
    """structure_json_fixed 컬럼이 없으면 생성"""
    if check_column_exists():
        print("✅ structure_json_fixed 컬럼이 이미 존재합니다.")
        return True
    
    print("📝 structure_json_fixed 컬럼을 생성합니다...")
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            query = """
                ALTER TABLE design_objects
                ADD COLUMN structure_json_fixed JSONB
            """
            cur.execute(query)
            conn.commit()
            print("✅ 컬럼 생성 완료!")
            return True
    except Exception as e:
        conn.rollback()
        print(f"❌ 컬럼 생성 실패: {e}")
        return False
    finally:
        conn.close()


# ============================================================
# 파일 처리 함수
# ============================================================
def load_structure_json_fixed(folder_path: Path) -> Optional[Dict]:
    """폴더에서 structure_json_fixed.json 로드"""
    file_path = folder_path / "structure_json_fixed.json"
    
    if not file_path.exists():
        return None
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"    ❌ 파일 로드 실패: {e}")
        return None


def get_ids_from_directory(dir_path: Path) -> List[int]:
    """디렉토리 내 폴더명에서 ID 추출 (structure_json_fixed.json이 있는 폴더만)"""
    ids = []
    if not dir_path.exists():
        print(f"❌ 디렉토리를 찾을 수 없습니다: {dir_path}")
        return ids
    
    for item in dir_path.iterdir():
        if item.is_dir():
            try:
                object_id = int(item.name)
                # structure_json_fixed.json 파일이 있는지 확인
                if (item / "structure_json_fixed.json").exists():
                    ids.append(object_id)
                else:
                    print(f"  ⚠️ {item.name}: structure_json_fixed.json 없음, 건너뜀")
            except ValueError:
                pass  # 숫자가 아닌 폴더는 무시
    
    ids.sort()
    return ids


def parse_id_list(args: List[str]) -> List[int]:
    """인자에서 ID 리스트 파싱"""
    ids = []
    for arg in args:
        parts = arg.replace(',', ' ').split()
        for part in parts:
            try:
                ids.append(int(part))
            except ValueError:
                print(f"  ⚠️ '{part}'은(는) 숫자가 아니므로 건너뜁니다.")
    return ids


# ============================================================
# 메인 처리 함수
# ============================================================
def process_single(object_id: int, data_dir: Path, dry_run: bool = False) -> bool:
    """단일 ID 처리"""
    folder_path = data_dir / str(object_id)
    
    if not folder_path.exists():
        print(f"    ❌ 폴더를 찾을 수 없습니다: {folder_path}")
        return False
    
    structure = load_structure_json_fixed(folder_path)
    if structure is None:
        print(f"    ❌ structure_json_fixed.json을 찾을 수 없습니다")
        return False
    
    return update_structure_json_fixed(object_id, structure, dry_run)


def process_multiple(object_ids: List[int], data_dir: Path, dry_run: bool = False) -> Dict:
    """여러 ID 처리"""
    total = len(object_ids)
    success = 0
    failed = []
    skipped = []
    
    print("=" * 60)
    print(f"🚀 structure_json_fixed DB 업데이트")
    print(f"   총 {total}개 ID 처리 예정")
    print(f"   데이터 폴더: {data_dir}")
    if dry_run:
        print(f"   ⚠️  DRY-RUN 모드 (실제 업데이트 없음)")
    print("=" * 60)
    
    for i, object_id in enumerate(object_ids, 1):
        print(f"\n[{i}/{total}] ID: {object_id}")
        
        folder_path = data_dir / str(object_id)
        if not folder_path.exists():
            print(f"    ⚠️ 폴더 없음, 건너뜀")
            skipped.append(object_id)
            continue
        
        structure = load_structure_json_fixed(folder_path)
        if structure is None:
            print(f"    ⚠️ structure_json_fixed.json 없음, 건너뜀")
            skipped.append(object_id)
            continue
        
        try:
            result = update_structure_json_fixed(object_id, structure, dry_run)
            if result:
                success += 1
                print(f"    ✅ 성공")
            else:
                failed.append(object_id)
        except Exception as e:
            failed.append(object_id)
            print(f"    ❌ 오류: {e}")
    
    print("\n" + "=" * 60)
    print(f"📊 처리 완료!")
    print(f"   성공: {success}/{total}")
    print(f"   실패: {len(failed)}/{total}")
    print(f"   건너뜀: {len(skipped)}/{total}")
    if failed:
        print(f"   실패한 ID: {failed[:10]}{'...' if len(failed) > 10 else ''}")
    if dry_run:
        print(f"\n   ⚠️  DRY-RUN 모드였습니다. 실제 업데이트하려면 --dry-run 옵션을 제거하세요.")
    print("=" * 60)
    
    return {
        'total': total,
        'success': success,
        'failed': failed,
        'skipped': skipped
    }


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description='structure_json_fixed를 DB에 업데이트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 단일 ID
  python update_structure_json_fixed.py 283782
  
  # 여러 ID
  python update_structure_json_fixed.py 283782,283725,277457
  
  # 특정 디렉토리의 모든 폴더
  python update_structure_json_fixed.py --dir /path/to/data
  
  # dry-run (실제 업데이트 없이 확인만)
  python update_structure_json_fixed.py --dir ./data --dry-run
        """
    )
    
    parser.add_argument('ids', nargs='*', help='업데이트할 디자인 오브젝트 ID')
    parser.add_argument('--dir', '-d', type=str, help='데이터 디렉토리 경로')
    parser.add_argument('--dry-run', action='store_true', help='실제 업데이트 없이 확인만')
    parser.add_argument('--create-column', action='store_true', help='컬럼이 없으면 생성')
    
    args = parser.parse_args()
    
    # 컬럼 생성 옵션
    if args.create_column:
        if not create_column_if_not_exists():
            sys.exit(1)
    
    # 데이터 디렉토리 설정
    if args.dir:
        data_dir = Path(args.dir)
    else:
        data_dir = Path(__file__).parent / "data"
    
    # ID 수집
    object_ids = []
    
    if args.dir:
        # --dir 옵션으로 폴더에서 ID 추출
        print(f"📂 디렉토리에서 ID 추출: {data_dir}")
        object_ids = get_ids_from_directory(data_dir)
        print(f"  → {len(object_ids)}개 ID 발견 (structure_json_fixed.json 있는 폴더)")
    elif args.ids:
        # 인자로 전달된 ID
        object_ids = parse_id_list(args.ids)
    
    if not object_ids:
        if not args.create_column:
            parser.print_help()
        sys.exit(0 if args.create_column else 1)
    
    # 컬럼 존재 확인
    if not check_column_exists():
        print("❌ structure_json_fixed 컬럼이 존재하지 않습니다.")
        print("   --create-column 옵션으로 컬럼을 먼저 생성하세요.")
        print("   예: python update_structure_json_fixed.py --create-column")
        sys.exit(1)
    
    # 처리
    if len(object_ids) == 1:
        success = process_single(object_ids[0], data_dir, args.dry_run)
        sys.exit(0 if success else 1)
    else:
        result = process_multiple(object_ids, data_dir, args.dry_run)
        sys.exit(0 if not result['failed'] else 1)


if __name__ == "__main__":
    main()
