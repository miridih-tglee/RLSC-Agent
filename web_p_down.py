#!/usr/bin/env python3
"""WebP 이미지를 다운로드하여 PNG로 변환하는 스크립트"""

import httpx
from PIL import Image
from io import BytesIO
from pathlib import Path

# 다운로드할 URL
URL = "https://image-similarity-check-staging.miricanvas.com/structured-content/def6080d-9f88-4f6f-954b-02391709cc3b/page.webp"

# 저장할 파일명 (data 폴더에 저장)
OUTPUT_PATH = Path(__file__).parent / "data" / "downloaded_page.png"


def download_webp_as_png(url: str, output_path: Path) -> None:
    """WebP 이미지를 다운로드하여 PNG로 저장"""
    print(f"다운로드 중: {url}")
    
    # 이미지 다운로드
    response = httpx.get(url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    
    # WebP를 PNG로 변환
    image = Image.open(BytesIO(response.content))
    
    # 출력 폴더가 없으면 생성
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # PNG로 저장
    image.save(output_path, "PNG")
    print(f"저장 완료: {output_path}")
    print(f"이미지 크기: {image.size[0]}x{image.size[1]}")


if __name__ == "__main__":
    download_webp_as_png(URL, OUTPUT_PATH)
