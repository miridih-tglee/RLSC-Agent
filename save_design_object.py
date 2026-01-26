#!/usr/bin/env python3
"""
Design Object JSON 저장 스크립트

DB에서 design_object 데이터를 가져와서 JSON 파일로 저장합니다.
(structure_json 수정 로직 없이 원본 그대로 저장)

사용법:
    # 단일 ID
    python save_design_object.py 283782
    
    # 여러 ID (쉼표 또는 공백으로 구분)
    python save_design_object.py 283782,283725,277457
    python save_design_object.py 283782 283725 277457
    
    # 폴더 경로 (폴더 내 디렉토리명을 ID로 사용)
    python save_design_object.py --dir /path/to/folder
    
    # 출력 폴더 지정
    python save_design_object.py --dir /path/to/folder --output /path/to/output
    
    # 썸네일 다운로드 건너뛰기
    python save_design_object.py 283782 --skip-thumbnail
"""

import sys
import json
from pathlib import Path
from typing import Dict, List, Optional

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2가 설치되어 있지 않습니다. 설치해주세요:")
    print("  pip install psycopg2-binary")
    sys.exit(1)

try:
    import httpx
    from PIL import Image
    from io import BytesIO
    THUMBNAIL_SUPPORT = True
except ImportError:
    THUMBNAIL_SUPPORT = False


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

COLUMNS = [
    "id",
    "uuid",
    "origin_size_thumbnail_url",
    "structure_json",
    "content_signature",
    "content_signature_sorted",
    "design_object_meta"
]


# ============================================================
# DB 함수
# ============================================================
def fetch_design_object(object_id: int) -> Optional[dict]:
    """DB에서 design_object 데이터 조회"""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = f"""
                SELECT {', '.join(COLUMNS)}
                FROM design_objects
                WHERE id = %s
            """
            cur.execute(query, (object_id,))
            result = cur.fetchone()
            return dict(result) if result else None
    finally:
        conn.close()


# ============================================================
# 파일 저장 함수
# ============================================================
def save_json(data, output_path: Path, name: str) -> bool:
    """JSON 파일 저장"""
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음")
        return False
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {output_path.name}")
    return True


def save_text(data: str, output_path: Path, name: str) -> bool:
    """텍스트 파일 저장"""
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음")
        return False
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(data)
    print(f"  ✅ {output_path.name}")
    return True


def download_thumbnail(url: str, output_path: Path) -> bool:
    """썸네일 다운로드 (webp -> png, 투명 배경은 흰색으로)"""
    if not THUMBNAIL_SUPPORT:
        print("  ⚠️  httpx 또는 Pillow가 설치되어 있지 않아 썸네일 다운로드를 건너뜁니다.")
        return False
    
    if not url:
        print("  ⚠️  썸네일 URL이 없습니다.")
        return False
    
    try:
        print(f"  📥 다운로드 중: {url[:60]}...")
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        
        image = Image.open(BytesIO(response.content))
        
        if image.mode == "RGBA":
            white_bg = Image.new("RGB", image.size, (255, 255, 255))
            white_bg.paste(image, mask=image.split()[3])
            image = white_bg
            print("  🎨 투명 배경 → 흰색 배경")
        elif image.mode != "RGB":
            image = image.convert("RGB")
        
        image.save(output_path, "PNG")
        print(f"  ✅ {output_path.name} ({image.size[0]}x{image.size[1]})")
        return True
    except Exception as e:
        print(f"  ❌ 다운로드 실패: {e}")
        return False


# ============================================================
# 단일 오브젝트 저장
# ============================================================
def save_single_object(
    object_id: int, 
    output_base_dir: Path = None, 
    skip_thumbnail: bool = False,
    verbose: bool = True
) -> bool:
    """
    단일 Design Object의 JSON 파일들 저장
    
    Args:
        object_id: 처리할 디자인 오브젝트 ID
        output_base_dir: 출력 기본 폴더 (None이면 기본 data 폴더 사용)
        skip_thumbnail: 썸네일 다운로드 건너뛰기
        verbose: 상세 로그 출력 여부
    
    Returns:
        성공 여부
    """
    if verbose:
        print("=" * 60)
        print(f"💾 Design Object 저장: ID={object_id}")
        print("=" * 60)
    
    # 1. DB에서 데이터 조회
    if verbose:
        print(f"\n📥 Step 1: DB에서 데이터 조회")
    data = fetch_design_object(object_id)
    
    if not data:
        print(f"  ❌ id={object_id}에 해당하는 데이터를 찾을 수 없습니다.")
        return False
    
    if verbose:
        print(f"  ✅ 데이터 찾음! uuid: {data.get('uuid')}")
    
    # 2. 출력 폴더 생성
    if output_base_dir is None:
        output_base_dir = Path(__file__).parent / "data"
    output_dir = output_base_dir / str(object_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"\n📁 출력 폴더: {output_dir}")
    
    # 3. 썸네일 다운로드
    if not skip_thumbnail:
        if verbose:
            print("\n📷 Step 2: 썸네일 다운로드")
        download_thumbnail(
            data.get("origin_size_thumbnail_url"),
            output_dir / "thumbnail.png"
        )
    
    # 4. JSON 파일들 저장
    if verbose:
        print("\n📄 Step 3: JSON 파일 저장")
    
    save_json(data.get("structure_json"), output_dir / "structure_json.json", "structure_json")
    save_json(data.get("content_signature"), output_dir / "content_signature.json", "content_signature")
    save_text(data.get("content_signature_sorted"), output_dir / "content_signature_sorted.txt", "content_signature_sorted")
    save_json(data.get("design_object_meta"), output_dir / "design_object_meta.json", "design_object_meta")
    
    # info.json 저장 (uuid, id)
    uuid_data = {
        "id": object_id,
        "uuid": str(data.get("uuid")) if data.get("uuid") else None
    }
    save_json(uuid_data, output_dir / "info.json", "info")
    
    if verbose:
        print(f"\n🎉 완료! ID={object_id}")
    
    return True


def save_multiple_objects(
    object_ids: List[int], 
    output_base_dir: Path = None, 
    skip_thumbnail: bool = False,
    verbose: bool = False
) -> Dict:
    """
    여러 Design Object 저장
    
    Args:
        object_ids: 처리할 디자인 오브젝트 ID 리스트
        output_base_dir: 출력 기본 폴더
        skip_thumbnail: 썸네일 다운로드 건너뛰기
        verbose: 각 오브젝트 상세 로그 출력 여부
    
    Returns:
        처리 결과 통계
    """
    total = len(object_ids)
    success = 0
    failed = []
    
    print("=" * 60)
    print(f"💾 Design Object 일괄 저장")
    print(f"   총 {total}개 ID 처리 예정")
    if output_base_dir:
        print(f"   출력 폴더: {output_base_dir}")
    if skip_thumbnail:
        print(f"   ℹ️  썸네일 다운로드: 건너뜀")
    print("=" * 60)
    
    for i, object_id in enumerate(object_ids, 1):
        print(f"\n[{i}/{total}] Saving ID: {object_id}")
        try:
            result = save_single_object(
                object_id, 
                output_base_dir, 
                skip_thumbnail=skip_thumbnail,
                verbose=verbose
            )
            if result:
                success += 1
                print(f"  ✅ 성공")
            else:
                failed.append(object_id)
                print(f"  ❌ 실패")
        except Exception as e:
            failed.append(object_id)
            print(f"  ❌ 오류: {e}")
    
    print("\n" + "=" * 60)
    print(f"📊 저장 완료!")
    print(f"   성공: {success}/{total}")
    print(f"   실패: {len(failed)}/{total}")
    if failed:
        print(f"   실패한 ID: {failed[:10]}{'...' if len(failed) > 10 else ''}")
    print("=" * 60)
    
    return {
        'total': total,
        'success': success,
        'failed': failed
    }


# ============================================================
# 유틸리티 함수
# ============================================================
def get_ids_from_directory(dir_path: Path) -> List[int]:
    """디렉토리 내 폴더명에서 ID 추출"""
    ids = []
    if not dir_path.exists():
        print(f"❌ 디렉토리를 찾을 수 없습니다: {dir_path}")
        return ids
    
    for item in dir_path.iterdir():
        if item.is_dir():
            try:
                ids.append(int(item.name))
            except ValueError:
                print(f"  ⚠️ '{item.name}'은(는) 숫자가 아니므로 건너뜁니다.")
    
    ids.sort()
    return ids


def parse_id_list(args: List[str]) -> List[int]:
    """인자에서 ID 리스트 파싱 (쉼표, 공백 구분 지원)"""
    ids = []
    for arg in args:
        # 쉼표로 구분된 경우
        parts = arg.replace(',', ' ').split()
        for part in parts:
            try:
                ids.append(int(part))
            except ValueError:
                print(f"  ⚠️ '{part}'은(는) 숫자가 아니므로 건너뜁니다.")
    return ids


# ============================================================
# 메인
# ============================================================
def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Design Object JSON 저장 스크립트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 단일 ID
  python save_design_object.py 283782
  
  # 여러 ID (쉼표 또는 공백으로 구분)
  python save_design_object.py 283782,283725,277457
  python save_design_object.py 283782 283725 277457
  
  # 폴더 경로 (폴더 내 디렉토리명을 ID로 사용)
  python save_design_object.py --dir /path/to/folder
  
  # 출력 폴더 지정
  python save_design_object.py --dir /path/to/folder --output /path/to/output
  
  # 썸네일 다운로드 건너뛰기
  python save_design_object.py 283782 --skip-thumbnail
  
  # 상세 로그 출력
  python save_design_object.py --dir /path/to/folder -v
        """
    )
    
    parser.add_argument('ids', nargs='*', help='처리할 디자인 오브젝트 ID (쉼표 또는 공백으로 구분)')
    parser.add_argument('--dir', '-d', type=str, help='ID를 추출할 디렉토리 경로')
    parser.add_argument('--output', '-o', type=str, help='출력 디렉토리 경로')
    parser.add_argument('--skip-thumbnail', '-s', action='store_true', help='썸네일 다운로드 건너뛰기')
    parser.add_argument('--verbose', '-v', action='store_true', help='상세 로그 출력')
    
    args = parser.parse_args()
    
    # 출력 디렉토리 설정
    output_dir = Path(args.output) if args.output else None
    
    # ID 수집
    object_ids = []
    
    # 1. --dir 옵션으로 폴더에서 ID 추출
    if args.dir:
        dir_path = Path(args.dir)
        print(f"📂 디렉토리에서 ID 추출: {dir_path}")
        object_ids = get_ids_from_directory(dir_path)
        print(f"  → {len(object_ids)}개 ID 발견")
    
    # 2. 인자로 전달된 ID
    elif args.ids:
        object_ids = parse_id_list(args.ids)
    
    # ID가 없으면 도움말 출력
    if not object_ids:
        parser.print_help()
        sys.exit(1)
    
    # 처리
    if len(object_ids) == 1:
        # 단일 ID 처리
        success = save_single_object(
            object_ids[0], 
            output_dir, 
            skip_thumbnail=args.skip_thumbnail,
            verbose=True
        )
        sys.exit(0 if success else 1)
    else:
        # 여러 ID 처리
        result = save_multiple_objects(
            object_ids, 
            output_dir, 
            skip_thumbnail=args.skip_thumbnail,
            verbose=args.verbose
        )
        sys.exit(0 if result['failed'] == [] else 1)


if __name__ == "__main__":
    main()
