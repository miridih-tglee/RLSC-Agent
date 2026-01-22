#!/usr/bin/env python3
"""
design_objects 테이블에서 id로 검색하여 데이터를 가져오는 스크립트

사용법:
    python fetch_design_object.py <id>
    python fetch_design_object.py 123
"""

import sys
import json
import httpx
from pathlib import Path
from PIL import Image
from io import BytesIO

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2가 설치되어 있지 않습니다. 설치해주세요:")
    print("  pip install psycopg2-binary")
    sys.exit(1)

# DB 연결 정보
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 54322,
    "user": "postgres",
    "password": "postgres",
    "dbname": "postgres"
}

# 가져올 컬럼들
COLUMNS = [
    "id",
    "uuid",
    "origin_size_thumbnail_url",
    "structure_json",
    "content_signature",
    "content_signature_sorted",
    "design_object_meta"
]


def fetch_design_object(object_id: int) -> dict:
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


def download_thumbnail(url: str, output_path: Path) -> bool:
    """썸네일 다운로드 (webp -> png, 투명 배경은 흰색으로)"""
    if not url:
        print("  ⚠️  썸네일 URL이 없습니다.")
        return False
    
    try:
        print(f"  📥 썸네일 다운로드 중: {url[:80]}...")
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        
        # WebP를 PNG로 변환
        image = Image.open(BytesIO(response.content))
        
        # 투명 배경(RGBA)이면 흰색 배경으로 합성
        if image.mode == "RGBA":
            # 흰색 배경 이미지 생성
            white_bg = Image.new("RGB", image.size, (255, 255, 255))
            # 알파 채널을 마스크로 사용하여 합성
            white_bg.paste(image, mask=image.split()[3])
            image = white_bg
            print("  🎨 투명 배경 → 흰색 배경으로 변환")
        elif image.mode != "RGB":
            # 다른 모드(P, L 등)도 RGB로 변환
            image = image.convert("RGB")
        
        image.save(output_path, "PNG")
        print(f"  ✅ 저장 완료: {output_path.name} ({image.size[0]}x{image.size[1]})")
        return True
    except Exception as e:
        print(f"  ❌ 다운로드 실패: {e}")
        return False


def save_json(data, output_path: Path, name: str) -> None:
    """JSON 파일 저장 (indent 포함)"""
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음 (NULL)")
        return
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 저장 완료: {output_path.name}")


def save_text(data: str, output_path: Path, name: str) -> None:
    """텍스트 파일 저장"""
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음 (NULL)")
        return
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(data)
    print(f"  ✅ 저장 완료: {output_path.name}")


def main():
    if len(sys.argv) < 2:
        print("사용법: python fetch_design_object.py <id>")
        print("예시: python fetch_design_object.py 123")
        sys.exit(1)
    
    try:
        object_id = int(sys.argv[1])
    except ValueError:
        print(f"오류: '{sys.argv[1]}'은(는) 유효한 숫자가 아닙니다.")
        sys.exit(1)
    
    print(f"\n🔍 design_objects 테이블에서 id={object_id} 검색 중...")
    
    # DB에서 데이터 조회
    data = fetch_design_object(object_id)
    
    if not data:
        print(f"❌ id={object_id}에 해당하는 데이터를 찾을 수 없습니다.")
        sys.exit(1)
    
    print(f"✅ 데이터 찾음! uuid: {data.get('uuid')}")
    
    # 출력 폴더 생성
    output_dir = Path(__file__).parent / "data" / str(object_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📁 출력 폴더: {output_dir}")
    
    # 썸네일 다운로드
    print("\n📷 썸네일 다운로드:")
    download_thumbnail(
        data.get("origin_size_thumbnail_url"),
        output_dir / "thumbnail.png"
    )
    
    # JSON 파일들 저장
    print("\n📄 JSON 파일 저장:")
    
    # structure_json
    save_json(data.get("structure_json"), output_dir / "structure_json.json", "structure_json")
    
    # content_signature
    save_json(data.get("content_signature"), output_dir / "content_signature.json", "content_signature")
    
    # content_signature_sorted (텍스트)
    save_text(data.get("content_signature_sorted"), output_dir / "content_signature_sorted.txt", "content_signature_sorted")
    
    # design_object_meta
    save_json(data.get("design_object_meta"), output_dir / "design_object_meta.json", "design_object_meta")
    
    # uuid (별도 파일로)
    uuid_data = {"uuid": str(data.get("uuid")) if data.get("uuid") else None, "id": object_id}
    save_json(uuid_data, output_dir / "info.json", "info")
    
    print(f"\n🎉 완료! 모든 파일이 {output_dir}에 저장되었습니다.")


if __name__ == "__main__":
    main()
