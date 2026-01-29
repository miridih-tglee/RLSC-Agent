#!/usr/bin/env python3
"""
Design Object 처리 파이프라인

1. DB에서 design_object 데이터 가져오기
2. 썸네일 다운로드 (WebP → PNG)
3. structure_json 수정 (겹침 수정, padding/gap 계산)
4. 모든 파일 저장

사용법:
    # 단일 ID
    python process_design_object.py 283782

    # 여러 ID (쉼표 또는 공백으로 구분)
    python process_design_object.py 283782,283725,277457
    python process_design_object.py 283782 283725 277457

    # 폴더 경로 (폴더 내 디렉토리명을 ID로 사용)
    python process_design_object.py --dir /path/to/folder

    # 출력 폴더 지정
    python process_design_object.py --dir /path/to/folder --output /path/to/output
"""

import sys                          # 시스템 종료(sys.exit) 및 인자 처리용
import json                         # JSON 직렬화/역직렬화
import uuid as uuid_lib             # 새 Group 노드 생성 시 고유 ID 발급
import httpx                        # HTTP 클라이언트 (썸네일 다운로드)
from pathlib import Path            # 파일/폴더 경로 객체
from copy import deepcopy           # 노드 수정 시 원본 보존을 위한 깊은 복사
from typing import Dict, List, Tuple, Optional  # 타입 힌트
from PIL import Image               # 이미지 포맷 변환 (WebP → PNG, RGBA → RGB)
from io import BytesIO              # 바이트 스트림 → PIL Image 변환용

# PostgreSQL 드라이버 임포트 (없으면 안내 메시지 출력 후 종료)
try:
    import psycopg2                 # PostgreSQL 연결 드라이버
    import psycopg2.extras          # RealDictCursor (결과를 dict로 반환)
except ImportError:
    print("psycopg2가 설치되어 있지 않습니다. 설치해주세요:")
    print("  pip install psycopg2-binary")
    sys.exit(1)


# ============================================================
# DB 설정
# ============================================================
# 로컬 PostgreSQL 연결 정보
DB_CONFIG = {
    "host": "127.0.0.1",       # 로컬호스트
    "port": 54322,             # 포트 (기본 5432가 아닌 커스텀 포트)
    "user": "postgres",        # 사용자명
    "password": "postgres",    # 비밀번호
    "dbname": "postgres"       # 데이터베이스명
}

# design_objects 테이블에서 조회할 컬럼 목록
COLUMNS = [
    "id",                          # PK (정수)
    "uuid",                        # 고유 식별자 (UUID)
    "origin_size_thumbnail_url",   # 원본 크기 썸네일 URL
    "structure_json",              # 디자인 구조 트리 (JSON) ← 핵심 처리 대상
    "content_signature",           # 콘텐츠 지문 (유사 디자인 검색용)
    "content_signature_sorted",    # 정렬된 콘텐츠 지문 (순서 무관 매칭용)
    "design_object_meta"           # 디자인 메타데이터
]


# ============================================================
# DB 함수
# ============================================================
def fetch_design_object(object_id: int) -> dict:
    """DB에서 design_object 데이터 1건 조회

    Args:
        object_id: 조회할 디자인 오브젝트의 PK(id)

    Returns:
        조회 결과 dict (없으면 None)
    """
    # 매 호출마다 새 커넥션 생성 (배치 스크립트이므로 풀링 불필요)
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        # RealDictCursor: 컬럼명을 key로 하는 dict 반환
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = f"""
                SELECT {', '.join(COLUMNS)}
                FROM design_objects
                WHERE id = %s
            """
            # %s 파라미터 바인딩으로 SQL 인젝션 방지
            cur.execute(query, (object_id,))
            result = cur.fetchone()  # 1건만 조회
            return dict(result) if result else None
    finally:
        conn.close()  # 예외 발생 여부와 관계없이 커넥션 반환


# ============================================================
# 파일 저장 함수
# ============================================================
def download_thumbnail(url: str, output_path: Path) -> bool:
    """썸네일 이미지 다운로드 후 PNG로 저장

    - WebP 등 다양한 포맷을 PNG로 통일
    - RGBA(투명 배경) → 흰색 배경 위에 합성하여 RGB로 변환

    Args:
        url: 썸네일 이미지 URL
        output_path: 저장할 PNG 파일 경로

    Returns:
        성공 여부
    """
    if not url:
        print("  ⚠️  썸네일 URL이 없습니다.")
        return False

    try:
        print(f"  📥 다운로드 중: {url[:60]}...")
        # follow_redirects: CDN 리다이렉트 따라감, timeout: 30초 제한
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()  # 4xx/5xx 에러 시 예외 발생

        # 바이트 데이터 → PIL Image 객체로 변환
        image = Image.open(BytesIO(response.content))

        # RGBA(투명 배경) 이미지 처리
        if image.mode == "RGBA":
            # 흰색 배경 이미지 생성 (같은 크기)
            white_bg = Image.new("RGB", image.size, (255, 255, 255))
            # 알파 채널(split()[3])을 마스크로 사용하여 합성
            # → 투명한 부분은 흰색, 불투명한 부분은 원본 색상
            white_bg.paste(image, mask=image.split()[3])
            image = white_bg
            print("  🎨 투명 배경 → 흰색 배경")
        elif image.mode != "RGB":
            # P(팔레트), L(그레이스케일) 등 → RGB 변환
            image = image.convert("RGB")

        # PNG 포맷으로 저장
        image.save(output_path, "PNG")
        print(f"  ✅ {output_path.name} ({image.size[0]}x{image.size[1]})")
        return True
    except Exception as e:
        print(f"  ❌ 다운로드 실패: {e}")
        return False


def save_json(data, output_path: Path, name: str) -> None:
    """JSON 데이터를 파일로 저장

    Args:
        data: 저장할 데이터 (dict/list)
        output_path: 저장 경로
        name: 로그 출력용 이름
    """
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음")
        return

    with open(output_path, "w", encoding="utf-8") as f:
        # ensure_ascii=False: 한글 등 유니코드를 그대로 저장 (이스케이프 안 함)
        # indent=2: 보기 좋게 들여쓰기
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {output_path.name}")


def save_text(data: str, output_path: Path, name: str) -> None:
    """텍스트 데이터를 파일로 저장

    Args:
        data: 저장할 문자열
        output_path: 저장 경로
        name: 로그 출력용 이름
    """
    if data is None:
        print(f"  ⚠️  {name}: 데이터 없음")
        return

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(data)
    print(f"  ✅ {output_path.name}")


# ============================================================
# Structure Fixer 유틸리티
# ============================================================
def generate_id() -> str:
    """새 Group 노드용 UUID4 문자열 생성"""
    return str(uuid_lib.uuid4())


def get_role(node: Dict) -> str:
    """노드의 role에서 마지막 부분만 추출

    예: "Role.Element.Decoration" → "Decoration"
        "Role.Title" → "Title"
        "" → ""
    """
    role = node.get('role', '')
    return role.split('.')[-1] if '.' in role else role


def get_type(node: Dict) -> str:
    """노드의 type 반환

    예: "SVG", "Text", "Frame", "Image", "Group", "HStack", "VStack" 등
    """
    return node.get('type', '')


def is_background(node: Dict) -> bool:
    """이 노드가 Background role인지 확인"""
    return get_role(node) == 'Background'


def is_decoration(node: Dict) -> bool:
    """이 노드가 Decoration role인지 확인
    (전체 role 문자열에 'Element.Decoration' 포함 여부로 판단)
    """
    return 'Element.Decoration' in node.get('role', '')


def is_marker(node: Dict) -> bool:
    """이 노드가 Marker role인지 확인"""
    return get_role(node) == 'Marker'


def is_frame(node: Dict) -> bool:
    """이 노드가 Frame type인지 확인"""
    return get_type(node) == 'Frame'


def is_image(node: Dict) -> bool:
    """이 노드가 Image type인지 확인"""
    return get_type(node) == 'Image'


def get_bbox(node: Dict) -> Optional[Tuple[float, float, float, float]]:
    """노드의 바운딩 박스(좌상단, 우하단) 반환

    Returns:
        (left, top, right, bottom) 또는 position이 없으면 None
    """
    pos = node.get('position', {})
    if not pos:
        return None
    x, y = pos.get('x', 0), pos.get('y', 0)
    w, h = pos.get('width', 0), pos.get('height', 0)
    return (x, y, x + w, y + h)  # (left, top, right, bottom)


def get_area(node: Dict) -> float:
    """노드의 면적 (width × height) 반환"""
    pos = node.get('position', {})
    return pos.get('width', 0) * pos.get('height', 0)


def is_overlapping(bbox1: Tuple, bbox2: Tuple, threshold: float = 0.1) -> bool:
    """두 바운딩 박스의 겹침 여부 판정

    판정 기준: 교집합 면적 / 더 작은 박스의 면적 > threshold
    - threshold=0.1 → 작은 박스의 10% 이상 겹쳐야 겹침으로 인정
    - 미세한 접촉(1~2px)은 무시됨

    Args:
        bbox1: (left, top, right, bottom)
        bbox2: (left, top, right, bottom)
        threshold: 겹침 비율 임계값 (기본 10%)

    Returns:
        겹침 여부
    """
    # 교집합 영역의 좌상단/우하단 계산
    x1 = max(bbox1[0], bbox2[0])  # 교집합 왼쪽 = 두 왼쪽 경계 중 큰 값
    y1 = max(bbox1[1], bbox2[1])  # 교집합 위쪽 = 두 위쪽 경계 중 큰 값
    x2 = min(bbox1[2], bbox2[2])  # 교집합 오른쪽 = 두 오른쪽 경계 중 작은 값
    y2 = min(bbox1[3], bbox2[3])  # 교집합 아래쪽 = 두 아래쪽 경계 중 작은 값

    # 교집합이 존재하지 않는 경우 (떨어져 있음)
    if x1 >= x2 or y1 >= y2:
        return False

    # 교집합 면적 계산
    intersection = (x2 - x1) * (y2 - y1)
    # 각 박스의 면적 계산
    area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
    area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
    # 더 작은 박스의 면적 (비율 계산의 분모)
    smaller_area = min(area1, area2)

    # 면적이 0 이하인 경우 (점/선 형태의 노드) → 겹침 아님
    if smaller_area <= 0:
        return False

    # 교집합 비율이 임계값 초과하면 겹침
    return intersection / smaller_area > threshold


# ============================================================
# 겹침 검사
# ============================================================
def should_check_pair(node1: Dict, node2: Dict) -> bool:
    """두 노드가 겹침 검사 대상인지 판별

    검사 제외 대상:
    - Background role (배경은 겹침 허용)
    - 컨테이너 type (Group, HStack, VStack, ZStack, Grid)
    - Title, Description, Subtitle role (텍스트 콘텐츠)

    검사 대상 조합:
    - Decoration ↔ Decoration
    - Decoration ↔ Marker
    - Marker ↔ Marker

    ※ type이 Text여도 role이 Decoration/Marker면 검사 대상
    """
    role1, role2 = get_role(node1), get_role(node2)
    type1, type2 = get_type(node1), get_type(node2)

    # [제외 1] Background는 다른 모든 요소와 겹침 허용 → 검사 안 함
    if role1 == 'Background' or role2 == 'Background':
        return False

    # [제외 2] 컨테이너 타입은 검사 제외
    # → 컨테이너는 자식들의 묶음이므로 겹침 검사 의미 없음
    # → 검사하면 무한 재귀 위험 (Group 안에 또 Group)
    container_types = ['Group', 'HStack', 'VStack', 'ZStack', 'Grid']
    if type1 in container_types or type2 in container_types:
        return False

    # [제외 3] Title, Description, Subtitle role은 제외
    # → 텍스트 콘텐츠는 의도적으로 다른 요소 위에 올라가는 경우가 많음
    # ※ type이 Text여도 role이 Decoration/Marker면 여기서 안 걸리고 아래 검사 대상이 됨
    if role1 in ['Title', 'Description', 'Subtitle']:
        return False
    if role2 in ['Title', 'Description', 'Subtitle']:
        return False

    # [검사 대상] Decoration/Marker끼리의 조합만 검사
    # Decoration ↔ Decoration
    if role1 == 'Decoration' and role2 == 'Decoration':
        return True
    # Decoration ↔ Marker (양방향)
    if (role1 == 'Decoration' and role2 == 'Marker') or (role1 == 'Marker' and role2 == 'Decoration'):
        return True
    # Marker ↔ Marker
    if role1 == 'Marker' and role2 == 'Marker':
        return True

    # 그 외 모든 조합은 검사 안 함 (예: Highlight ↔ Decoration 등)
    return False


def find_overlapping_pairs(children: List[Dict]) -> List[Tuple[int, int]]:
    """자식 노드들 중 겹치는 쌍(인덱스)을 모두 찾기

    O(n²) 브루트포스로 모든 쌍 검사
    - should_check_pair로 검사 대상 필터링
    - is_overlapping으로 실제 겹침 판정

    Returns:
        겹치는 쌍의 인덱스 리스트 [(i, j), ...]
    """
    pairs = []
    for i in range(len(children)):
        bbox_i = get_bbox(children[i])
        if not bbox_i:  # position이 없는 노드는 건너뜀
            continue
        for j in range(i + 1, len(children)):  # i보다 뒤의 요소만 (중복 방지)
            bbox_j = get_bbox(children[j])
            if not bbox_j:
                continue
            # 검사 대상이면서 실제 겹치는 쌍만 추가
            if should_check_pair(children[i], children[j]) and is_overlapping(bbox_i, bbox_j):
                pairs.append((i, j))
    return pairs


def group_overlapping(children: List[Dict], pairs: List[Tuple[int, int]]) -> List[List[int]]:
    """겹치는 쌍들을 Union-Find로 그룹화

    전이적 관계를 처리: A↔B 겹침, B↔C 겹침 → [A,B,C] 한 그룹
    경로 압축(path compression) 적용으로 거의 O(n) 성능

    Args:
        children: 자식 노드 리스트 (인덱스 참조용)
        pairs: 겹치는 쌍의 인덱스 리스트

    Returns:
        그룹별 인덱스 리스트 [[0,1,2], [3,4], ...]
        (2개 이상인 그룹만 반환)
    """
    if not pairs:
        return []

    # Union-Find 초기화: 각 노드가 자기 자신을 부모로
    parent = list(range(len(children)))

    def find(x):
        """루트 노드 찾기 (경로 압축 적용)
        경로 압축: find 과정에서 만나는 모든 노드를 루트에 직접 연결
        → 이후 find가 O(1)에 가까워짐
        """
        if parent[x] != x:
            parent[x] = find(parent[x])  # 경로 압축
        return parent[x]

    def union(x, y):
        """두 노드를 같은 그룹으로 합치기"""
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py  # x의 루트를 y의 루트에 연결

    # 모든 겹침 쌍에 대해 union 수행
    for i, j in pairs:
        union(i, j)

    # 같은 루트를 가진 노드들을 그룹으로 묶기
    groups = {}
    for i, j in pairs:
        for idx in [i, j]:
            root = find(idx)
            if root not in groups:
                groups[root] = set()
            groups[root].add(idx)

    # 2개 이상인 그룹만 반환 (1개짜리는 그룹화 불필요)
    return [list(g) for g in groups.values() if len(g) >= 2]


# ============================================================
# 그룹 묶기
# ============================================================
def wrap_in_group(nodes: List[Dict]) -> Dict:
    """겹치는 노드들을 새 Group 컨테이너로 래핑

    1. 가장 큰 요소를 Background로 선정 (SVG 우선)
    2. 전체 바운딩 박스를 Group의 position으로
    3. 나머지 요소들은 원래 role 유지

    Background 후보 제외: Text, Frame, Image 타입
    우선순위: SVG > 기타 타입 (같은 우선순위 내에서 면적 최대)

    Args:
        nodes: 래핑할 노드 리스트

    Returns:
        새로 생성된 Group 노드 dict
    """
    if not nodes:
        return {}

    # ---- Background 후보 탐색 ----
    max_area, bg_idx = -1, -1          # 전체 타입 중 최대 면적/인덱스
    max_svg_area, svg_idx = -1, -1     # SVG 타입 중 최대 면적/인덱스

    for i, node in enumerate(nodes):
        node_type = get_type(node)
        # Text, Frame, Image는 Background가 될 수 없음 → 건너뜀
        if node_type in ['Text', 'Frame', 'Image']:
            continue
        area = get_area(node)

        # SVG는 별도 추적 (Background 우선순위 최상위)
        if node_type == 'SVG' and area > max_svg_area:
            max_svg_area, svg_idx = area, i

        # 전체 최대도 추적 (SVG가 없을 때의 폴백)
        if area > max_area:
            max_area, bg_idx = area, i

    # SVG가 있으면 SVG 최대를 BG로, 없으면 전체 최대를 BG로
    if svg_idx >= 0:
        bg_idx = svg_idx

    # ---- 전체 바운딩 박스 계산 (Group의 position이 됨) ----
    all_bboxes = [get_bbox(n) for n in nodes if get_bbox(n)]
    if all_bboxes:
        min_x = min(b[0] for b in all_bboxes)  # 가장 왼쪽
        min_y = min(b[1] for b in all_bboxes)  # 가장 위쪽
        max_x = max(b[2] for b in all_bboxes)  # 가장 오른쪽
        max_y = max(b[3] for b in all_bboxes)  # 가장 아래쪽
    else:
        # bbox가 하나도 없는 경우 기본값 (발생 가능성 낮음)
        min_x = min_y = 0
        max_x = max_y = 100

    # ---- 자식 노드 복사 & Background 지정 ----
    wrapped_children = []
    for i, node in enumerate(nodes):
        node_copy = deepcopy(node)
        # BG로 선정된 노드만 role 변경 (나머지는 원래 role 유지)
        if i == bg_idx and bg_idx >= 0:
            node_copy['role'] = 'Role.Element.Background'
        wrapped_children.append(node_copy)

    # ---- 새 Group 노드 생성 ----
    return {
        'id': generate_id(),                        # 새 UUID
        'role': 'Role.LayoutContainer.Decoration',  # 장식용 레이아웃 컨테이너
        'type': 'Group',                            # 타입은 Group
        'children': wrapped_children,               # 래핑된 자식들
        'position': {
            'x': round(min_x, 2),                   # 전체 바운딩 박스의 좌상단 x
            'y': round(min_y, 2),                   # 전체 바운딩 박스의 좌상단 y
            'width': round(max_x - min_x, 2),       # 전체 바운딩 박스의 너비
            'height': round(max_y - min_y, 2)        # 전체 바운딩 박스의 높이
        }
    }


def fix_multiple_backgrounds(children: List[Dict]) -> List[Dict]:
    """Background가 2개 이상일 때 가장 큰 것만 유지, 나머지는 Decoration으로 강등

    Args:
        children: 자식 노드 리스트

    Returns:
        수정된 자식 노드 리스트 (Background는 최대 1개)
    """
    # Background role인 자식들의 인덱스 수집
    backgrounds = [i for i, c in enumerate(children) if is_background(c)]
    # 0개 또는 1개면 수정 불필요
    if len(backgrounds) <= 1:
        return children

    # 면적 기준 내림차순 정렬 → 가장 큰 것을 유지 대상으로
    bg_areas = [(i, get_area(children[i])) for i in backgrounds]
    bg_areas.sort(key=lambda x: x[1], reverse=True)
    largest_bg_idx = bg_areas[0][0]  # 면적 최대인 Background의 인덱스

    # 가장 큰 것 제외한 나머지 Background → Decoration으로 강등
    result = []
    for i, child in enumerate(children):
        child_copy = deepcopy(child)
        if i in backgrounds and i != largest_bg_idx:
            child_copy['role'] = 'Role.Element.Decoration'  # 강등
        result.append(child_copy)

    return result


def find_background_candidate(children: List[Dict]) -> int:
    """현재 자식들 중 Background로 승격할 후보 찾기

    후보 조건:
    - role이 Decoration 또는 Marker
    - type이 Text, Frame, Image가 아닐 것
    - 이미 Background가 아닐 것

    선정 우선순위: SVG > 기타 타입 (면적 최대)

    ※ wrap_in_group과의 차이:
       - wrap_in_group: 이미 BG가 없음이 보장된 상태에서 호출
       - 이 함수: 전체 children에서 찾으므로 기존 BG 제외 필요

    Args:
        children: 자식 노드 리스트

    Returns:
        후보 인덱스 (-1이면 후보 없음)
    """
    max_area, max_idx = -1, -1          # 전체 후보 중 최대
    max_svg_area, svg_idx = -1, -1      # SVG 후보 중 최대

    for i, child in enumerate(children):
        node_type = get_type(child)
        # Text, Frame, Image 타입은 Background 후보에서 제외
        if node_type in ['Text', 'Frame', 'Image']:
            continue
        # 이미 Background인 노드는 제외 (중복 방지)
        if is_background(child):
            continue
        # Decoration 또는 Marker role만 후보 (Title 등은 안 됨)
        role = get_role(child)
        if role in ['Decoration', 'Marker']:
            area = get_area(child)

            # SVG는 별도 추적 (우선순위 최상위)
            if node_type == 'SVG' and area > max_svg_area:
                max_svg_area, svg_idx = area, i

            # 전체 최대도 추적
            if area > max_area:
                max_area, max_idx = area, i

    # SVG가 있으면 SVG 우선, 없으면 전체 최대
    if svg_idx >= 0:
        return svg_idx
    return max_idx


# ============================================================
# Frame/Image → Marker 변환
# ============================================================
def convert_frame_image_to_marker(node: Dict) -> Dict:
    """트리 전체에서 Frame과 Image의 role을 Marker로 변환

    변환 이유:
    - Frame: 이미지를 담는 클리핑 마스크 → 플레이스홀더(Marker) 의미
    - Image: 샘플/더미 이미지 → 교체 가능한 자리(Marker) 의미
    - Marker로 통일해야 이후 겹침 검사에서 일관되게 처리 가능

    변환 규칙:
    - Frame → role을 Marker로 변경, 내부 Image도 Marker로
    - 단독 Image → role을 Marker로 변경
    - 재귀적으로 모든 자손에 적용

    ※ Frame 내부 Image는 2번 처리됨 (432줄에서 직접 + 444줄에서 재귀)
       결과는 동일하므로 버그는 아니지만 중복 작업
    """
    result = deepcopy(node)
    node_type = get_type(result)

    # Frame 타입이면 자신과 내부 Image의 role을 Marker로
    if node_type == 'Frame':
        result['role'] = 'Role.Element.Marker'

        # Frame의 직속 자식 중 Image를 Marker로 (재귀 전 직접 처리)
        children = result.get('children', [])
        for child in children:
            if get_type(child) == 'Image':
                child['role'] = 'Role.Element.Marker'

    # 단독 Image도 Marker로
    if node_type == 'Image':
        result['role'] = 'Role.Element.Marker'

    # 모든 자식에 대해 재귀적으로 동일 변환 적용
    children = result.get('children', [])
    if children:
        result['children'] = [convert_frame_image_to_marker(c) for c in children]

    return result


# ============================================================
# 메인 수정 함수
# ============================================================
MAX_RECURSION_DEPTH = 50  # 무한 재귀 방지용 최대 깊이 (정상 디자인은 10~20 수준)

def fix_node(node: Dict, depth: int = 0, verbose: bool = True) -> Dict:
    """노드 트리의 겹침을 수정하는 핵심 함수 (재귀, bottom-up)

    처리 순서:
    1. 자식들 재귀 처리 (가장 깊은 곳부터 → bottom-up)
    2. Background 중복 수정 (1차)
    3. Text와 겹치는 Decoration → Background 승격 (Marker 제외!)
    4. Decoration/Marker 겹침 검사
    5. 겹침 처리: BG 승격 또는 Group 래핑
    7. Background 중복 수정 (2차 - 최종 정리)

    Args:
        node: 처리할 노드
        depth: 현재 재귀 깊이
        verbose: 로그 출력 여부

    Returns:
        수정된 노드 (deepcopy)
    """
    # ---- 재귀 깊이 제한 체크 ----
    # 50 초과면 무한 재귀 버그로 판단, 원본 그대로 반환
    if depth > MAX_RECURSION_DEPTH:
        if verbose:
            print(f"⚠️ 최대 재귀 깊이 초과 (depth={depth}), 더 이상 처리하지 않음")
        return deepcopy(node)

    indent = "    " * min(depth, 10)  # 로그 들여쓰기 (최대 10단계)
    result = deepcopy(node)           # 원본 보존을 위한 깊은 복사
    children = result.get('children', [])

    # 리프 노드(자식 없음)는 수정할 것이 없으므로 즉시 반환
    if not children:
        return result

    if verbose:
        node_id = node.get('id', 'unknown')[:20]
        print(f"{indent}📁 {node_id} ({get_type(node)})")

    # ---- 단계 1: 자식들 먼저 재귀 처리 (bottom-up) ----
    # 첫 번째 자식부터 순차적으로, 각 자식의 끝까지 내려갔다 올라옴 (DFS)
    # → 이 시점 이후 모든 자식은 "정리 완료" 상태
    children = [fix_node(c, depth + 1, verbose) for c in children]

    # ---- 단계 2: Background 중복 수정 (1차) ----
    # 자식 재귀 처리 중 새 Background가 생겼을 수 있으므로 정리
    # 가장 큰 Background만 유지, 나머지는 Decoration으로 강등
    children = fix_multiple_backgrounds(children)

    # ---- 단계 3: Text와 겹치는 Decoration → Background 승격 ----
    # 목적: "텍스트 뒤에 깔린 장식 도형"을 의미적으로 Background로 인식
    # 조건: 현재 컨테이너에 Background가 아직 없을 때만
    existing_bg = any(is_background(c) for c in children)
    if not existing_bg:
        # Title, Description, Subtitle, Highlight 역할의 자식 수집
        text_roles = ['Title', 'Description', 'Subtitle', 'Highlight']
        text_children = [c for c in children if get_role(c) in text_roles]

        if text_children:
            # Decoration 중 텍스트와 겹치는 가장 큰 것 찾기
            best_deco_idx = -1     # 최적 후보 인덱스
            best_deco_area = 0     # 최적 후보 면적

            for deco_idx, deco in enumerate(children):
                deco_role = get_role(deco)
                deco_type = get_type(deco)

                # ※ Decoration만 대상! Marker는 이 단계에서 제외됨
                if deco_role != 'Decoration':
                    continue
                # Text, Frame, Image 타입은 배경이 될 수 없으므로 제외
                if deco_type in ['Text', 'Frame', 'Image']:
                    continue

                deco_bbox = get_bbox(deco)
                if not deco_bbox:
                    continue

                # 이 Decoration이 텍스트 자식 중 하나라도 겹치는지 확인
                for text in text_children:
                    text_bbox = get_bbox(text)
                    if not text_bbox:
                        continue
                    if is_overlapping(deco_bbox, text_bbox):
                        # 겹치는 Decoration 중 가장 큰 것을 기록
                        pos = deco.get('position', {})
                        area = pos.get('width', 0) * pos.get('height', 0)
                        if area > best_deco_area:
                            best_deco_area = area
                            best_deco_idx = deco_idx
                        break  # 한 텍스트라도 겹치면 이 Deco는 후보 확정, 다음 Deco로

            # 후보가 있으면 Background로 승격
            if best_deco_idx >= 0:
                children[best_deco_idx] = deepcopy(children[best_deco_idx])
                children[best_deco_idx]['role'] = 'Role.Element.Background'
                if verbose:
                    print(f"{indent}   🎨 Text와 겹치는 Deco → BG")

    # ---- 단계 4: Decoration/Marker 끼리 겹침 검사 ----
    # should_check_pair로 필터링된 쌍만 실제 겹침 판정
    pairs = find_overlapping_pairs(children)

    # ---- 단계 5: 겹침이 있을 때 처리 ----
    if pairs:
        # 현재 Background 존재 여부에 따라 분기
        existing_bg = any(is_background(c) for c in children)

        if not existing_bg:
            # [케이스 A] Background 없음
            # → 가장 큰 Decoration/Marker를 Background로 승격
            bg_idx = find_background_candidate(children)
            if bg_idx >= 0:
                children[bg_idx] = deepcopy(children[bg_idx])
                children[bg_idx]['role'] = 'Role.Element.Background'
                if verbose:
                    print(f"{indent}   🎨 겹침 발견 → 가장 큰 Deco → BG")

            # Background로 승격된 노드는 should_check_pair에서 제외되므로
            # 다시 겹침 검사하면 쌍이 줄어듦
            pairs = find_overlapping_pairs(children)
        else:
            # [케이스 B] Background 있음
            # → 승격 없이 바로 Group 묶기로 진행
            if verbose:
                print(f"{indent}   ℹ️ 기존 Background 존재 → 바로 Group 묶기")

        # ---- 단계 6: 아직 겹침이 남아있으면 Group으로 묶기 ----
        if pairs:
            # Union-Find로 겹치는 요소들을 그룹화
            groups = group_overlapping(children, pairs)
            if groups:
                # 그룹에 속하는 인덱스들 수집
                grouped = set()
                for g in groups:
                    grouped.update(g)

                # 그룹에 속하지 않는 자식들은 그대로 유지
                new_children = [c for i, c in enumerate(children) if i not in grouped]

                # 각 그룹을 Group 노드로 래핑
                for group_indices in groups:
                    group_nodes = [children[i] for i in group_indices]
                    new_group = wrap_in_group(group_nodes)
                    # ※ 새로 생성된 Group 내부도 재귀적으로 fix 적용
                    # (Group 내부에서 또 겹침이 있을 수 있으므로)
                    new_group = fix_node(new_group, depth + 1, verbose)
                    # ※ Group은 new_children 뒤에 append → 원래 순서와 달라질 수 있음
                    new_children.append(new_group)
                    if verbose:
                        print(f"{indent}   📦 Group 생성 및 내부 수정: {len(group_nodes)}개")

                children = new_children

    # ---- 단계 7: Background 중복 수정 (2차 - 최종 정리) ----
    # 단계 3~6에서 새 Background가 추가됐을 수 있으므로 최종 확인
    children = fix_multiple_backgrounds(children)
    result['children'] = children
    return result


# ============================================================
# 좌표 변환
# ============================================================
def to_absolute_coords(node: Dict, parent_x: float = 0, parent_y: float = 0) -> Dict:
    """상대좌표 → 절대좌표 변환 (재귀)

    각 노드의 position.x/y에 부모의 절대좌표를 더함
    → 모든 노드가 동일한 전역 좌표계를 사용하게 됨
    → 서로 다른 부모의 자식 간 겹침 비교가 정확해짐

    width/height는 좌표계에 무관하므로 변경하지 않음

    Args:
        node: 변환할 노드
        parent_x: 부모의 절대 x좌표
        parent_y: 부모의 절대 y좌표
    """
    result = deepcopy(node)
    pos = result.get('position', {})

    if pos:
        # 절대좌표 = 부모 절대좌표 + 자신의 상대좌표
        abs_x = parent_x + pos.get('x', 0)
        abs_y = parent_y + pos.get('y', 0)
        pos['x'], pos['y'] = abs_x, abs_y  # 덮어쓰기
    else:
        # position이 없으면 부모 좌표를 그대로 자식에게 전달
        abs_x, abs_y = parent_x, parent_y

    # 자식들에게 현재 절대좌표를 전달하며 재귀 변환
    children = result.get('children', [])
    if children:
        result['children'] = [to_absolute_coords(c, abs_x, abs_y) for c in children]

    return result


def to_relative_coords(node: Dict, parent_x: float = 0, parent_y: float = 0) -> Dict:
    """절대좌표 → 상대좌표 변환 (재귀, to_absolute_coords의 역변환)

    각 노드의 position.x/y에서 부모의 절대좌표를 빼서 상대좌표로 복원
    소수점 2자리까지 반올림 (0.01px 이하 오차 무시)

    Args:
        node: 변환할 노드
        parent_x: 부모의 절대 x좌표
        parent_y: 부모의 절대 y좌표
    """
    result = deepcopy(node)
    pos = result.get('position', {})

    if pos:
        # 현재 절대좌표를 먼저 읽고
        abs_x, abs_y = pos.get('x', 0), pos.get('y', 0)
        # 부모 절대좌표를 빼서 상대좌표로 복원
        pos['x'] = round(abs_x - parent_x, 2)
        pos['y'] = round(abs_y - parent_y, 2)
    else:
        abs_x, abs_y = parent_x, parent_y

    # 자식들에게 현재 절대좌표를 전달하며 재귀 변환
    # (abs_x/abs_y는 변환 전의 절대좌표 = 자식 입장의 부모 절대좌표)
    children = result.get('children', [])
    if children:
        result['children'] = [to_relative_coords(c, abs_x, abs_y) for c in children]

    return result


# ============================================================
# Alignment 계산
# ============================================================
def calculate_alignment(child_pos: Dict, parent_width: float, parent_height: float, threshold: float = 0.05) -> Tuple[str, str]:
    """단일 자식의 정렬 방향 계산

    부모 내에서 자식의 좌우/상하 여백을 비교하여 정렬 판단
    허용 오차: max(부모 크기의 5%, 10px)

    판정 로직:
    - |left - right| ≤ threshold → "center"
    - left < right - threshold → "left"
    - 그 외 → "right"
    (수직도 동일 로직)

    Args:
        child_pos: 자식의 position dict {x, y, width, height}
        parent_width: 부모의 너비
        parent_height: 부모의 높이
        threshold: 여백 비율 허용 오차 (기본 5%)

    Returns:
        (horizontalAlignment, verticalAlignment) 튜플
        예: ("center", "top")
    """
    x = child_pos.get('x', 0)
    y = child_pos.get('y', 0)
    w = child_pos.get('width', 0)
    h = child_pos.get('height', 0)

    # 네 방향 여백 계산
    left_margin = x                        # 왼쪽 여백
    right_margin = parent_width - (x + w)  # 오른쪽 여백
    top_margin = y                         # 위쪽 여백
    bottom_margin = parent_height - (y + h)  # 아래쪽 여백

    # 허용 오차 계산 (5% 또는 10px 중 큰 값)
    # → 부모가 200px이면 10px, 400px이면 20px
    h_thresh = max(parent_width * threshold, 10)
    v_thresh = max(parent_height * threshold, 10)

    # 수평 정렬 판정
    if abs(left_margin - right_margin) <= h_thresh:
        h_align = "center"    # 좌우 여백 차이가 오차 범위 내 → 중앙
    elif left_margin < right_margin - h_thresh:
        h_align = "left"      # 왼쪽 여백이 확실히 작음 → 왼쪽 정렬
    else:
        h_align = "right"     # 오른쪽 여백이 작음 → 오른쪽 정렬

    # 수직 정렬 판정 (동일 로직)
    if abs(top_margin - bottom_margin) <= v_thresh:
        v_align = "center"
    elif top_margin < bottom_margin - v_thresh:
        v_align = "top"
    else:
        v_align = "bottom"

    return h_align, v_align


def add_alignment_to_containers(node: Dict, verbose: bool = False) -> Dict:
    """컨테이너 노드에 alignment 속성 추가 (재귀)

    컨테이너 타입별 계산 방식:
    - HStack: horizontal은 첫/마지막 자식의 좌우 여백으로, vertical은 투표(다수결)로
    - VStack: vertical은 첫/마지막 자식의 상하 여백으로, horizontal은 투표(다수결)로
    - Group/ZStack/Grid: 둘 다 투표(다수결)로

    alignment 계산에 참여하는 타입 (alignable_types):
    - SVG, Image, Text, VStack, HStack, ZStack, Group, Grid
    - Frame은 제외 (Marker로 변환되므로 실질적으로 영향 없음)

    추가되는 속성:
    - horizontalAlignment: "left" | "center" | "right"
    - verticalAlignment: "top" | "center" | "bottom"
    - alignment: HStack은 vertical값, VStack은 leading/center/trailing로 매핑
    """
    result = deepcopy(node)
    node_type = get_type(result)
    children = result.get('children', [])
    position = result.get('position', {})

    # alignment를 계산할 컨테이너 타입
    container_types = ['VStack', 'HStack', 'ZStack', 'Group', 'Grid']
    # alignment 계산에 참여하는 자식 타입 (리프 노드 + 컨테이너)
    alignable_types = ['SVG', 'Image', 'Text', 'VStack', 'HStack', 'ZStack', 'Group', 'Grid']

    # 컨테이너이고 자식이 있을 때만 alignment 계산
    if node_type in container_types and children:
        parent_width = position.get('width', 0)
        parent_height = position.get('height', 0)

        # 부모 크기가 유효할 때만 (0이면 계산 불가)
        if parent_width > 0 and parent_height > 0:
            # position이 있는 alignable 자식만 필터링
            # (Background 포함 - alignment 계산에서는 제외하지 않음)
            alignable_children = [
                c for c in children
                if get_type(c) in alignable_types and c.get('position', {})
            ]

            if alignable_children:
                # 오차 범위 계산
                h_thresh = max(parent_width * 0.05, 10)
                v_thresh = max(parent_height * 0.05, 10)

                if node_type == 'HStack':
                    # ---- HStack: 가로 나열 컨테이너 ----

                    # [horizontal] 첫 번째/마지막 자식의 좌우 여백으로 판단
                    # → HStack에서 자식들이 가로로 나열되므로,
                    #   전체 블록의 좌우 여백 = 첫 자식의 left + 마지막 자식의 right
                    first_pos = alignable_children[0].get('position', {})
                    last_pos = alignable_children[-1].get('position', {})

                    left_margin = first_pos.get('x', 0)
                    right_margin = parent_width - (last_pos.get('x', 0) + last_pos.get('width', 0))

                    if abs(left_margin - right_margin) <= h_thresh:
                        result['horizontalAlignment'] = "center"
                    elif left_margin < right_margin - h_thresh:
                        result['horizontalAlignment'] = "left"
                    else:
                        result['horizontalAlignment'] = "right"

                    # [vertical] 각 자식의 vertical alignment를 투표(다수결)로 결정
                    # → 교차축(cross axis) 정렬은 자식마다 다를 수 있으므로 다수결
                    v_votes = {}
                    for child in alignable_children:
                        child_pos = child.get('position', {})
                        _, v = calculate_alignment(child_pos, parent_width, parent_height)
                        v_votes[v] = v_votes.get(v, 0) + 1
                    if v_votes:
                        v_align = max(v_votes, key=v_votes.get)  # 최다 투표
                        result['verticalAlignment'] = v_align
                        # HStack의 alignment = 교차축(vertical) 정렬값
                        result['alignment'] = v_align

                elif node_type == 'VStack':
                    # ---- VStack: 세로 나열 컨테이너 ----

                    # [vertical] 첫 번째/마지막 자식의 상하 여백으로 판단
                    first_pos = alignable_children[0].get('position', {})
                    last_pos = alignable_children[-1].get('position', {})

                    top_margin = first_pos.get('y', 0)
                    bottom_margin = parent_height - (last_pos.get('y', 0) + last_pos.get('height', 0))

                    if abs(top_margin - bottom_margin) <= v_thresh:
                        result['verticalAlignment'] = "center"
                    elif top_margin < bottom_margin - v_thresh:
                        result['verticalAlignment'] = "top"
                    else:
                        result['verticalAlignment'] = "bottom"

                    # [horizontal] 각 자식의 horizontal alignment를 투표로 결정
                    h_votes = {}
                    for child in alignable_children:
                        child_pos = child.get('position', {})
                        h, _ = calculate_alignment(child_pos, parent_width, parent_height)
                        h_votes[h] = h_votes.get(h, 0) + 1
                    if h_votes:
                        h_align = max(h_votes, key=h_votes.get)
                        result['horizontalAlignment'] = h_align
                        # VStack의 alignment = 교차축(horizontal) 정렬값
                        # SwiftUI 규약으로 매핑: left→leading, right→trailing
                        # (RTL 언어 지원을 위해 방향 중립적 용어 사용)
                        alignment_map = {'left': 'leading', 'center': 'center', 'right': 'trailing'}
                        result['alignment'] = alignment_map.get(h_align, 'leading')

                else:
                    # ---- Group, ZStack, Grid: 둘 다 투표 ----
                    # 주축/교차축 구분이 없으므로 양쪽 모두 다수결
                    h_votes = {}
                    v_votes = {}
                    for child in alignable_children:
                        child_pos = child.get('position', {})
                        h, v = calculate_alignment(child_pos, parent_width, parent_height)
                        h_votes[h] = h_votes.get(h, 0) + 1
                        v_votes[v] = v_votes.get(v, 0) + 1
                    if h_votes:
                        result['horizontalAlignment'] = max(h_votes, key=h_votes.get)
                    if v_votes:
                        result['verticalAlignment'] = max(v_votes, key=v_votes.get)
                    # ※ Group/ZStack/Grid에는 alignment 필드를 설정하지 않음

                if verbose:
                    print(f"    [{node_type}] {result.get('id', '')[:15]} -> "
                          f"h={result.get('horizontalAlignment')}, v={result.get('verticalAlignment')}")

    # 자식들에 대해 재귀 처리 (top-down)
    # ※ fix_node와 달리 top-down: 부모 먼저 처리하고 자식 처리
    #   (alignment 계산은 순서 무관하므로 문제 없음)
    if children:
        result['children'] = [add_alignment_to_containers(c, verbose) for c in children]

    return result


# ============================================================
# Layout Properties
# ============================================================
def add_layout_properties(node: Dict) -> Dict:
    """컨테이너에 direction, padding, gap 속성 추가 (재귀)

    추가되는 속성:
    - direction: HStack→"horizontal", VStack→"vertical"
    - padding: { top, bottom, left, right } (Background 제외한 콘텐츠 기준)
    - gap: 인접 자식 간 평균 거리 (HStack/VStack만, 음수는 무시)
    """
    result = deepcopy(node)
    node_type = get_type(result)

    # ---- direction 추가 ----
    if node_type == 'HStack':
        result['direction'] = 'horizontal'  # 가로 나열
    elif node_type == 'VStack':
        result['direction'] = 'vertical'    # 세로 나열

    children = result.get('children', [])
    if not children:
        return result

    # 자식들 먼저 재귀 처리 (bottom-up)
    result['children'] = [add_layout_properties(c) for c in children]
    children = result['children']  # 재귀 처리된 자식들로 갱신

    # ---- padding 계산 ----
    parent_pos = result.get('position', {})
    parent_w, parent_h = parent_pos.get('width', 0), parent_pos.get('height', 0)

    if parent_w > 0 and parent_h > 0 and children:
        # Background를 제외한 콘텐츠 자식들의 바운딩 박스
        # → Background는 보통 부모 전체를 덮으므로 포함하면 padding이 0이 됨
        content_children = [c for c in children if not is_background(c)]
        child_bboxes = [get_bbox(c) for c in content_children if get_bbox(c)]

        # 콘텐츠 자식이 없으면 (전부 Background) 전체 자식으로 fallback
        if not child_bboxes:
            child_bboxes = [get_bbox(c) for c in children if get_bbox(c)]

        if child_bboxes:
            # 콘텐츠 전체의 바운딩 박스
            min_x = min(b[0] for b in child_bboxes)  # 가장 왼쪽 콘텐츠
            min_y = min(b[1] for b in child_bboxes)  # 가장 위쪽 콘텐츠
            max_x = max(b[2] for b in child_bboxes)  # 가장 오른쪽 콘텐츠
            max_y = max(b[3] for b in child_bboxes)  # 가장 아래쪽 콘텐츠

            # padding = 부모 테두리에서 콘텐츠 바운딩 박스까지의 거리
            # max(0, ...)로 음수 방지 (자식이 부모 밖으로 삐져나간 경우)
            result['padding'] = {
                'top': round(max(0, min_y), 2),
                'bottom': round(max(0, parent_h - max_y), 2),
                'left': round(max(0, min_x), 2),
                'right': round(max(0, parent_w - max_x), 2)
            }

    # ---- gap 계산 (HStack/VStack만, 자식 2개 이상) ----
    if len(children) >= 2 and node_type in ['HStack', 'VStack']:
        # Background를 제외한 콘텐츠 자식들로 gap 계산
        content_children = [c for c in children if not is_background(c)]

        if len(content_children) >= 2:
            gaps = []
            # HStack이면 x축, VStack이면 y축 기준으로 정렬
            key = 'x' if node_type == 'HStack' else 'y'
            sorted_children = sorted(content_children, key=lambda c: c.get('position', {}).get(key, 0))

            # 인접 자식 간 거리 계산
            for i in range(len(sorted_children) - 1):
                bbox1, bbox2 = get_bbox(sorted_children[i]), get_bbox(sorted_children[i + 1])
                if bbox1 and bbox2:
                    # HStack: 다음 자식의 left - 현재 자식의 right
                    # VStack: 다음 자식의 top - 현재 자식의 bottom
                    gap = (bbox2[0] - bbox1[2]) if node_type == 'HStack' else (bbox2[1] - bbox1[3])
                    # 음수(겹침)는 무시, 양수(간격)만 수집
                    if gap > 0:
                        gaps.append(gap)

            # 양수 gap들의 평균을 사용
            # 모든 gap이 음수(겹침)면 'gap' 키 자체가 추가되지 않음
            if gaps:
                result['gap'] = round(sum(gaps) / len(gaps), 2)

    return result


# ============================================================
# Structure 수정 파이프라인
# ============================================================
def fix_structure(structure: Dict, verbose: bool = True) -> Dict:
    """structure_json 수정 파이프라인 (전체 흐름)

    순서가 중요함:
    1. Marker 변환: role을 정규화해야 이후 겹침 검사가 일관됨
    2. 절대좌표: 서로 다른 부모의 자식 간 겹침 비교를 위해
    3. 겹침 수정: 절대좌표에서 정확한 겹침 판정
    4. 상대좌표: 원래 형식으로 복원
    5. layout properties: 상대좌표 기반 (부모 대비 거리)
    6. alignment: 상대좌표 기반 (부모 대비 위치)
    """
    # Step 1: Frame/Image → Marker 변환
    # → 모든 Frame과 Image의 role을 Marker로 통일
    if verbose:
        print("\n  🔄 Frame/Image → Marker 변환")
    structure = convert_frame_image_to_marker(structure)

    # Step 2: 상대좌표 → 절대좌표 변환
    # → 겹침 검사를 위해 전역 좌표계로 통일
    if verbose:
        print("  🔄 절대좌표 변환")
    structure_abs = to_absolute_coords(structure)

    # Step 3: 겹침 수정 (fix_node)
    # → Background 승격, 겹치는 요소 Group 래핑 등
    if verbose:
        print("  🔧 겹침 수정")
    fixed_abs = fix_node(structure_abs, verbose=verbose)

    # Step 4: 절대좌표 → 상대좌표 변환
    # → 원래의 부모 기준 상대좌표 형식으로 복원
    if verbose:
        print("  🔄 상대좌표 변환")
    fixed_rel = to_relative_coords(fixed_abs)

    # Step 5: padding/gap/direction 추가
    # → 상대좌표 기반으로 부모 내 여백과 자식 간 간격 계산
    if verbose:
        print("  📐 padding/gap/direction 추가")
    result = add_layout_properties(fixed_rel)

    # Step 6: alignment 추가
    # → 상대좌표 기반으로 컨테이너별 정렬 방향 결정
    if verbose:
        print("  📍 alignment 추가")
    result = add_alignment_to_containers(result, verbose=verbose)

    return result


# ============================================================
# 단일 오브젝트 처리
# ============================================================
def process_single_object(object_id: int, output_base_dir: Path = None, verbose: bool = True) -> bool:
    """단일 Design Object를 DB에서 가져와서 처리 후 파일로 저장

    처리 흐름:
    1. DB 조회
    2. 출력 폴더 생성
    3. 썸네일 다운로드 (PNG)
    4. 원본 JSON/텍스트 파일 저장
    5. structure_json 수정 (fix_structure 파이프라인)
    6. 수정된 structure_json_fixed.json 저장

    Args:
        object_id: 처리할 디자인 오브젝트 ID (DB의 PK)
        output_base_dir: 출력 기본 폴더 (None이면 스크립트 위치의 data/ 폴더)
        verbose: 상세 로그 출력 여부

    Returns:
        처리 성공 여부
    """
    if verbose:
        print("=" * 60)
        print(f"🚀 Design Object 처리: ID={object_id}")
        print("=" * 60)

    # ---- 1. DB에서 데이터 조회 ----
    if verbose:
        print(f"\n📥 Step 1: DB에서 데이터 조회")
    data = fetch_design_object(object_id)

    if not data:
        print(f"  ❌ id={object_id}에 해당하는 데이터를 찾을 수 없습니다.")
        return False

    if verbose:
        print(f"  ✅ 데이터 찾음! uuid: {data.get('uuid')}")

    # ---- 2. 출력 폴더 생성 ----
    if output_base_dir is None:
        # 기본 경로: 스크립트 파일과 같은 위치의 data/ 폴더
        output_base_dir = Path(__file__).parent / "data"
    output_dir = output_base_dir / str(object_id)  # data/{object_id}/
    output_dir.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"\n📁 출력 폴더: {output_dir}")

    # ---- 3. 썸네일 다운로드 ----
    if verbose:
        print("\n📷 Step 2: 썸네일 다운로드")
    download_thumbnail(
        data.get("origin_size_thumbnail_url"),
        output_dir / "thumbnail.png"
    )

    # ---- 4. 원본 JSON/텍스트 파일들 저장 ----
    if verbose:
        print("\n📄 Step 3: 원본 파일 저장")
    save_json(data.get("structure_json"), output_dir / "structure_json.json", "structure_json")
    save_json(data.get("content_signature"), output_dir / "content_signature.json", "content_signature")
    save_text(data.get("content_signature_sorted"), output_dir / "content_signature_sorted.txt", "content_signature_sorted")
    save_json(data.get("design_object_meta"), output_dir / "design_object_meta.json", "design_object_meta")

    # UUID와 ID 매핑 정보 저장
    uuid_data = {"uuid": str(data.get("uuid")) if data.get("uuid") else None, "id": object_id}
    save_json(uuid_data, output_dir / "info.json", "info")

    # ---- 5. Structure 수정 (핵심 파이프라인) ----
    if verbose:
        print("\n🔧 Step 4: Structure 수정")
    structure = data.get("structure_json")

    if structure:
        # fix_structure 파이프라인 실행
        fixed_structure = fix_structure(structure, verbose=verbose)

        # ---- 6. 수정된 Structure 저장 ----
        if verbose:
            print("\n💾 Step 5: 수정된 Structure 저장")
        save_json(fixed_structure, output_dir / "structure_json_fixed.json", "structure_json_fixed")
    else:
        print("  ⚠️ structure_json이 없어서 수정을 건너뜁니다.")

    if verbose:
        print(f"\n🎉 완료! ID={object_id}")

    return True


def process_multiple_objects(object_ids: List[int], output_base_dir: Path = None, verbose: bool = False) -> Dict:
    """여러 Design Object를 일괄 처리

    Args:
        object_ids: 처리할 ID 리스트
        output_base_dir: 출력 기본 폴더
        verbose: 각 오브젝트의 상세 로그 출력 여부

    Returns:
        { total: 전체 수, success: 성공 수, failed: 실패한 ID 리스트 }
    """
    total = len(object_ids)
    success = 0
    failed = []

    print("=" * 60)
    print(f"🚀 Design Object 일괄 처리")
    print(f"   총 {total}개 ID 처리 예정")
    if output_base_dir:
        print(f"   출력 폴더: {output_base_dir}")
    print("=" * 60)

    # 순차적으로 각 ID 처리
    for i, object_id in enumerate(object_ids, 1):
        print(f"\n[{i}/{total}] Processing ID: {object_id}")
        try:
            result = process_single_object(object_id, output_base_dir, verbose=verbose)
            if result:
                success += 1
                print(f"  ✅ 성공")
            else:
                failed.append(object_id)
                print(f"  ❌ 실패")
        except Exception as e:
            # 개별 오브젝트 실패가 전체 처리를 중단시키지 않음
            failed.append(object_id)
            print(f"  ❌ 오류: {e}")

    # ---- 결과 요약 출력 ----
    print("\n" + "=" * 60)
    print(f"📊 처리 완료!")
    print(f"   성공: {success}/{total}")
    print(f"   실패: {len(failed)}/{total}")
    if failed:
        # 실패 ID가 많으면 10개만 표시
        print(f"   실패한 ID: {failed[:10]}{'...' if len(failed) > 10 else ''}")
    print("=" * 60)

    return {
        'total': total,
        'success': success,
        'failed': failed
    }


def get_ids_from_directory(dir_path: Path) -> List[int]:
    """디렉토리 내 폴더명에서 ID 추출

    폴더명이 숫자인 것만 ID로 인식, 정렬 후 반환

    Args:
        dir_path: 탐색할 디렉토리 경로

    Returns:
        정렬된 ID 리스트
    """
    ids = []
    if not dir_path.exists():
        print(f"❌ 디렉토리를 찾을 수 없습니다: {dir_path}")
        return ids

    for item in dir_path.iterdir():
        if item.is_dir():  # 파일은 무시, 디렉토리만
            try:
                ids.append(int(item.name))  # 폴더명을 정수로 변환
            except ValueError:
                # 숫자가 아닌 폴더명은 건너뜀 (예: "temp", ".git")
                print(f"  ⚠️ '{item.name}'은(는) 숫자가 아니므로 건너뜁니다.")

    ids.sort()  # 오름차순 정렬
    return ids


def parse_id_list(args: List[str]) -> List[int]:
    """CLI 인자에서 ID 리스트 파싱

    쉼표와 공백 구분을 모두 지원:
    - "283782,283725,277457" → [283782, 283725, 277457]
    - "283782 283725 277457" → [283782, 283725, 277457]
    - 혼합도 가능: "283782,283725 277457"

    Args:
        args: CLI에서 받은 문자열 리스트

    Returns:
        파싱된 정수 ID 리스트
    """
    ids = []
    for arg in args:
        # 쉼표를 공백으로 치환 후 공백으로 분리 → 통일된 처리
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
    """CLI 엔트리포인트

    사용법:
      python process_design_object.py 283782                    # 단일 ID
      python process_design_object.py 283782,283725             # 쉼표 구분
      python process_design_object.py --dir /path/to/folder     # 폴더에서 ID 추출
      python process_design_object.py --dir /path --output /out # 출력 폴더 지정
      python process_design_object.py --dir /path -v            # 상세 로그
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='Design Object 처리 파이프라인',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 단일 ID
  python process_design_object.py 283782

  # 여러 ID (쉼표 또는 공백으로 구분)
  python process_design_object.py 283782,283725,277457
  python process_design_object.py 283782 283725 277457

  # 폴더 경로 (폴더 내 디렉토리명을 ID로 사용)
  python process_design_object.py --dir /path/to/folder

  # 출력 폴더 지정
  python process_design_object.py --dir /path/to/folder --output /path/to/output

  # 상세 로그 출력
  python process_design_object.py --dir /path/to/folder -v
        """
    )

    # CLI 인자 정의
    parser.add_argument('ids', nargs='*', help='처리할 디자인 오브젝트 ID (쉼표 또는 공백으로 구분)')
    parser.add_argument('--dir', '-d', type=str, help='ID를 추출할 디렉토리 경로')
    parser.add_argument('--output', '-o', type=str, help='출력 디렉토리 경로')
    parser.add_argument('--verbose', '-v', action='store_true', help='상세 로그 출력')

    args = parser.parse_args()

    # 출력 디렉토리 설정 (지정 안 하면 None → 기본값 사용)
    output_dir = Path(args.output) if args.output else None

    # ---- ID 수집 ----
    object_ids = []

    # 방법 1: --dir 옵션으로 폴더에서 ID 추출
    if args.dir:
        dir_path = Path(args.dir)
        print(f"📂 디렉토리에서 ID 추출: {dir_path}")
        object_ids = get_ids_from_directory(dir_path)
        print(f"  → {len(object_ids)}개 ID 발견")

    # 방법 2: 위치 인자로 직접 ID 전달
    elif args.ids:
        object_ids = parse_id_list(args.ids)

    # ID가 하나도 없으면 도움말 출력 후 종료
    if not object_ids:
        parser.print_help()
        sys.exit(1)

    # ---- 처리 실행 ----
    if len(object_ids) == 1:
        # 단일 ID: 상세 로그 기본 출력
        success = process_single_object(object_ids[0], output_dir, verbose=True)
        sys.exit(0 if success else 1)
    else:
        # 여러 ID: -v 옵션 있을 때만 상세 로그
        result = process_multiple_objects(object_ids, output_dir, verbose=args.verbose)
        sys.exit(0 if result['failed'] == [] else 1)


if __name__ == "__main__":
    main()
