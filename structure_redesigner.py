"""
Structure Redesigner: Flatten → Rebuild → Enrich 방식의 구조 재설계

기존 raw_data의 구조를 유지하는 대신,
1. 모든 요소를 절대좌표로 평탄화 (Flatten)
2. 이미지 + 요소 목록을 LLM에게 주고 새 구조 설계 (Design)
3. LLM 구조대로 JSON 재구성 (Rebuild)
4. 기존 Agent들로 세부 속성 설정 (Enrich) - resizing, layout, alignment
   - 병렬 처리 지원 (depth별로 동시 처리)
"""

import json
import base64
import yaml
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from copy import deepcopy

# 프롬프트 로더
from prompt_loader import PromptLoader


def encode_image_to_base64(image_path: str) -> Optional[str]:
    """이미지 파일을 base64로 인코딩"""
    try:
        path = Path(image_path)
        if not path.exists():
            print(f"⚠️ 이미지 파일을 찾을 수 없습니다: {image_path}")
            return None
        
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"⚠️ 이미지 인코딩 오류: {e}")
        return None


def get_image_mime_type(image_path: str) -> str:
    """이미지 MIME 타입 반환"""
    suffix = Path(image_path).suffix.lower()
    mime_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    return mime_types.get(suffix, 'image/png')


# =============================================================================
# Step 1: Flatten - 모든 요소를 절대좌표로 평탄화
# =============================================================================

def flatten_elements(node: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> List[Dict]:
    """
    raw_data의 모든 leaf 노드를 절대좌표로 변환하여 flat list로 반환
    
    Args:
        node: 현재 노드
        parent_abs_x: 부모의 절대 x 좌표
        parent_abs_y: 부모의 절대 y 좌표
    
    Returns:
        flat list of elements with absolute positions
    """
    result = []
    
    # 현재 노드의 상대 좌표
    pos = node.get('position', {})
    rel_x = pos.get('x', 0)
    rel_y = pos.get('y', 0)
    width = pos.get('width', 0)
    height = pos.get('height', 0)
    
    # 절대 좌표 계산
    abs_x = parent_abs_x + rel_x
    abs_y = parent_abs_y + rel_y
    
    children = node.get('children', [])
    
    if children:
        # 컨테이너 노드: 자식들을 재귀적으로 처리
        for child in children:
            result.extend(flatten_elements(child, abs_x, abs_y))
    else:
        # Leaf 노드: flat list에 추가
        element = {
            'id': node.get('id', ''),
            'type': node.get('type', ''),
            'original_role': node.get('role', ''),
            'abs_position': {
                'x': abs_x,
                'y': abs_y,
                'width': width,
                'height': height
            }
        }
        
        # 추가 속성들 복사
        if 'content' in node:
            element['content'] = node['content']
        if 'svgData' in node:
            element['svgData'] = node['svgData']
        if 'url' in node:
            element['url'] = node['url']
        if 'fontSize' in node:
            element['fontSize'] = node['fontSize']
        if 'fontFamily' in node:
            element['fontFamily'] = node['fontFamily']
        if 'fontWeight' in node:
            element['fontWeight'] = node['fontWeight']
        if 'areaSize' in node:
            element['areaSize'] = node['areaSize']
        
        result.append(element)
    
    return result


def get_elements_summary(flat_elements: List[Dict]) -> str:
    """flat elements를 LLM에게 전달할 요약 문자열로 변환"""
    lines = []
    for elem in flat_elements:
        pos = elem['abs_position']
        area = pos['width'] * pos['height']
        
        info = f"- id: {elem['id'][:16]}..."
        info += f"\n  type: {elem['type']}"
        info += f"\n  position: ({pos['x']:.0f}, {pos['y']:.0f}) size: {pos['width']:.0f}x{pos['height']:.0f}"
        info += f"\n  area: {area:.0f}"
        
        if elem.get('content'):
            content = elem['content'][:30] + '...' if len(elem.get('content', '')) > 30 else elem.get('content', '')
            info += f"\n  content: \"{content}\""
        
        lines.append(info)
    
    return "\n".join(lines)


# =============================================================================
# Step 2: LLM Structure Designer Agent
# =============================================================================

class LLMStructureDesignerAgent:
    """
    이미지 + flat 요소 목록을 보고 새로운 그룹 구조를 설계하는 에이전트
    prompts/structure_design.yaml 프롬프트 사용
    prompts/role_validation.yaml의 Role 정의를 참조
    """
    
    def __init__(self, llm_client, reference_image_path: Optional[str] = None):
        self.llm_client = llm_client
        self.reference_image_path = reference_image_path
        self.reference_image_base64 = None
        self.role_definitions = None
        self.design_prompts = None
        
        if reference_image_path:
            self.reference_image_base64 = encode_image_to_base64(reference_image_path)
            if self.reference_image_base64:
                print(f"📷 참조 이미지 로드 완료: {reference_image_path}")
        
        # 프롬프트 로드
        self._load_prompts()
    
    def _load_prompts(self):
        """YAML 프롬프트 파일들 로드"""
        try:
            # structure_design.yaml 로드
            design_path = Path(__file__).parent / 'prompts' / 'structure_design.yaml'
            with open(design_path, 'r', encoding='utf-8') as f:
                self.design_prompts = yaml.safe_load(f)
            print(f"📋 Design 프롬프트 로드 완료: {design_path}")
            
            # role_validation.yaml에서 Role 정의 로드
            role_path = Path(__file__).parent / 'prompts' / 'role_validation.yaml'
            with open(role_path, 'r', encoding='utf-8') as f:
                self.role_definitions = yaml.safe_load(f)
            print(f"📋 Role 정의 로드 완료: {role_path}")
        except Exception as e:
            print(f"⚠️ 프롬프트 로드 실패: {e}")
            self.design_prompts = {}
            self.role_definitions = {}
    
    def _get_role_definitions_text(self) -> str:
        """Role 정의를 텍스트로 변환"""
        if not self.role_definitions:
            return ""
        
        lines = ["## Role 정의 (role_validation.yaml 기준)\n"]
        
        # LayoutContainer roles
        lines.append("### LayoutContainer Roles (role 필드에 사용):")
        for role in self.role_definitions.get('role_definitions', {}).get('layout_container_roles', []):
            lines.append(f"- **{role['name']}**: {role['description']}")
        
        # Element roles
        lines.append("\n### Element Roles (role 필드에 사용):")
        for role in self.role_definitions.get('role_definitions', {}).get('element_roles', []):
            constraints = role.get('constraints', '')
            lines.append(f"- **{role['name']}**: {role['description']}")
            if constraints:
                lines.append(f"  - 제약: {constraints}")
        
        # Layout types - type 필드에 사용!
        lines.append("\n### Layout Types (type 필드에 사용):")
        lines.append("**그룹 노드의 `type` 필드에는 아래 값 중 하나를 사용:**")
        for lt in self.role_definitions.get('layout_type_definitions', []):
            lines.append(f"- **{lt['name']}**: {lt['description']}")
            lines.append(f"  - 조건: {lt.get('condition', '')}")
        
        lines.append("\n**예시:**")
        lines.append('- 가로 배열: `"type": "HStack"`')
        lines.append('- 세로 배열: `"type": "VStack"`')
        lines.append('- 불규칙/겹침: `"type": "Group"`')
        
        return "\n".join(lines)
    
    def design_structure(self, flat_elements: List[Dict]) -> Dict:
        """
        flat 요소 목록을 보고 새로운 계층 구조를 설계
        
        Returns:
            새로운 그룹 구조 (JSON)
        """
        prompt = self._create_prompt(flat_elements)
        response = self._call_llm(prompt)
        structure = self._parse_response(response)
        return structure
    
    def _create_prompt(self, flat_elements: List[Dict]) -> str:
        """구조 설계용 프롬프트 생성 (YAML에서 로드)"""
        elements_summary = get_elements_summary(flat_elements)
        role_definitions_text = self._get_role_definitions_text()
        element_ids = json.dumps([elem['id'] for elem in flat_elements], indent=2)
        
        # YAML에서 프롬프트 템플릿 로드
        if self.design_prompts and 'prompt_template' in self.design_prompts:
            template = self.design_prompts['prompt_template']
            prompt = template.format(
                task_description=self.design_prompts.get('task_description', ''),
                role_definitions=role_definitions_text,
                elements_summary=elements_summary,
                element_ids=element_ids,
                design_rules=self.design_prompts.get('design_rules', ''),
                output_format=self.design_prompts.get('output_format', ''),
                output_requirements=self.design_prompts.get('output_requirements', '')
            )
            return prompt
        
        # 폴백: 기본 프롬프트
        return f"""## 작업: 레이아웃 구조 재설계

{role_definitions_text}

### 입력 요소 목록
{elements_summary}

### 요소 ID
{element_ids}

JSON 구조로 출력하세요."""
    
    def _call_llm(self, prompt: str) -> str:
        """LLM 호출 (멀티모달, YAML 프롬프트 사용)"""
        if not self.llm_client:
            return '{}'
        
        # 멀티모달: 이미지가 있으면 함께 전송
        if self.reference_image_base64:
            mime_type = get_image_mime_type(self.reference_image_path)
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{self.reference_image_base64}",
                        "detail": "high"
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        else:
            user_content = prompt
        
        # YAML에서 system_role 로드
        system_message = self.design_prompts.get('system_role', '') if self.design_prompts else ''
        
        # YAML에서 LLM 설정 로드
        llm_config = self.design_prompts.get('llm_config', {}) if self.design_prompts else {}
        model = llm_config.get('model', 'gpt-4o')
        temperature = llm_config.get('temperature', 0.1)
        max_tokens = llm_config.get('max_tokens', 4000)
        
        response = self.llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_content}
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    
    def _parse_response(self, response: str) -> Dict:
        """LLM 응답 파싱"""
        try:
            # JSON 블록 추출
            if '```json' in response:
                json_start = response.find('```json') + 7
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            elif '```' in response:
                json_start = response.find('```') + 3
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            else:
                json_str = response.strip()
            
            return json.loads(json_str)
        except Exception as e:
            print(f"⚠️ 구조 파싱 오류: {e}")
            return {}


# =============================================================================
# Step 3: Rebuild - LLM 구조대로 JSON 재구성
# =============================================================================

def rebuild_json(flat_elements: List[Dict], structure: Dict) -> Dict:
    """
    LLM이 설계한 구조대로 JSON을 재구성
    
    Args:
        flat_elements: 평탄화된 요소 목록 (절대좌표)
        structure: LLM이 설계한 그룹 구조
    
    Returns:
        재구성된 JSON (상대좌표)
    """
    # 요소 ID → 요소 매핑
    element_map = {elem['id']: elem for elem in flat_elements}
    
    def build_node(node_spec: Dict, parent_abs_x: float = 0, parent_abs_y: float = 0) -> Dict:
        """노드 스펙을 실제 노드로 변환"""
        
        # Leaf 노드 (기존 요소 참조)
        if 'element_id' in node_spec:
            elem_id = node_spec['element_id']
            elem = element_map.get(elem_id)
            
            if not elem:
                print(f"⚠️ 요소를 찾을 수 없음: {elem_id}")
                return {}
            
            # 상대좌표 계산
            abs_pos = elem['abs_position']
            rel_x = abs_pos['x'] - parent_abs_x
            rel_y = abs_pos['y'] - parent_abs_y
            
            node = {
                'id': elem_id,
                'type': elem['type'],
                'role': node_spec.get('role', elem.get('original_role', '')),
                'position': {
                    'x': rel_x,
                    'y': rel_y,
                    'width': abs_pos['width'],
                    'height': abs_pos['height']
                }
            }
            
            # 추가 속성 복사
            for key in ['content', 'svgData', 'url', 'fontSize', 'fontFamily', 'fontWeight', 'areaSize']:
                if key in elem:
                    node[key] = elem[key]
            
            return node
        
        # 그룹 노드
        children_specs = node_spec.get('children', [])
        
        # 자식들의 절대좌표 범위 계산 (bounding box)
        child_positions = []
        for child_spec in children_specs:
            if 'element_id' in child_spec:
                elem = element_map.get(child_spec['element_id'])
                if elem:
                    child_positions.append(elem['abs_position'])
            else:
                # 중첩 그룹의 경우 재귀적으로 범위 계산
                nested_range = get_group_bounds(child_spec, element_map)
                if nested_range:
                    child_positions.append(nested_range)
        
        if not child_positions:
            return {}
        
        # 그룹의 절대좌표 (bounding box)
        group_abs_x = min(p['x'] for p in child_positions)
        group_abs_y = min(p['y'] for p in child_positions)
        group_max_x = max(p['x'] + p['width'] for p in child_positions)
        group_max_y = max(p['y'] + p['height'] for p in child_positions)
        group_width = group_max_x - group_abs_x
        group_height = group_max_y - group_abs_y
        
        # 자식들 빌드 (이 그룹의 절대좌표 기준 상대좌표)
        built_children = []
        for child_spec in children_specs:
            built_child = build_node(child_spec, group_abs_x, group_abs_y)
            if built_child:
                built_children.append(built_child)
        
        # 그룹의 상대좌표
        rel_x = group_abs_x - parent_abs_x
        rel_y = group_abs_y - parent_abs_y
        
        node = {
            'id': node_spec.get('id', f"group_{id(node_spec)}"),
            'type': node_spec.get('type', 'Group'),
            'role': node_spec.get('role', 'Role.LayoutContainer.Description'),
            'children': built_children,
            'position': {
                'x': rel_x,
                'y': rel_y,
                'width': group_width,
                'height': group_height
            },
            # 기본 레이아웃 속성
            'alignment': 'leading',
            'padding': {'top': 0, 'right': 0, 'bottom': 0, 'left': 0},
            'gap': 10
        }
        
        # direction 설정
        if node['type'] == 'VStack':
            node['direction'] = 'vertical'
        elif node['type'] == 'HStack':
            node['direction'] = 'horizontal'
        
        return node
    
    # root 노드 빌드
    root_spec = structure.get('root', structure)
    result = build_node(root_spec)
    
    return result


def get_group_bounds(node_spec: Dict, element_map: Dict) -> Optional[Dict]:
    """그룹 노드의 bounding box 계산"""
    children_specs = node_spec.get('children', [])
    
    positions = []
    for child_spec in children_specs:
        if 'element_id' in child_spec:
            elem = element_map.get(child_spec['element_id'])
            if elem:
                positions.append(elem['abs_position'])
        else:
            nested = get_group_bounds(child_spec, element_map)
            if nested:
                positions.append(nested)
    
    if not positions:
        return None
    
    return {
        'x': min(p['x'] for p in positions),
        'y': min(p['y'] for p in positions),
        'width': max(p['x'] + p['width'] for p in positions) - min(p['x'] for p in positions),
        'height': max(p['y'] + p['height'] for p in positions) - min(p['y'] for p in positions)
    }


# =============================================================================
# Main Pipeline
# =============================================================================

class StructureRedesigner:
    """
    전체 파이프라인: Flatten → Design → Rebuild → Enrich
    병렬 처리 지원 (depth별로 동시 처리)
    멀티모달 지원 (이미지 보고 속성 설정)
    """
    
    def __init__(self, llm_client, reference_image_path: Optional[str] = None,
                 max_concurrent: int = 10):
        self.llm_client = llm_client
        self.reference_image_path = reference_image_path
        self.designer = LLMStructureDesignerAgent(llm_client, reference_image_path)
        self.max_concurrent = max_concurrent
        
        # 멀티모달용 이미지 인코딩
        self.reference_image_base64 = None
        if reference_image_path:
            self.reference_image_base64 = encode_image_to_base64(reference_image_path)
        
        # 프롬프트 로더
        self.prompt_loader = PromptLoader()
    
    def redesign(self, raw_data: Dict, skip_enrich: bool = False, 
                 parallel: bool = False) -> Dict:
        """
        raw_data를 새로운 구조로 재설계
        
        Args:
            raw_data: 원본 JSON 데이터
            skip_enrich: Enrich 단계 스킵 여부
            parallel: 병렬 처리 사용 여부
        
        Returns:
            재설계된 JSON 데이터
        """
        print("\n" + "=" * 60)
        print("🔄 Structure Redesigner: Flatten → Design → Rebuild → Enrich")
        print("=" * 60)
        
        # Step 1: Flatten
        print("\n📋 Step 1: Flatten (절대좌표로 평탄화)")
        flat_elements = flatten_elements(raw_data)
        print(f"   → {len(flat_elements)}개 요소 추출")
        
        for elem in flat_elements[:5]:  # 처음 5개만 미리보기
            pos = elem['abs_position']
            print(f"   - {elem['id'][:16]}... ({elem['type']}) at ({pos['x']:.0f}, {pos['y']:.0f})")
        if len(flat_elements) > 5:
            print(f"   ... 외 {len(flat_elements) - 5}개")
        
        # Step 2: Design
        print("\n🎨 Step 2: Design (LLM 구조 설계)")
        structure = self.designer.design_structure(flat_elements)
        
        if not structure:
            print("   ⚠️ 구조 설계 실패, 원본 반환")
            return raw_data
        
        print("   → 새로운 구조 설계 완료")
        
        # Step 3: Rebuild
        print("\n🏗️ Step 3: Rebuild (상대좌표로 재구성)")
        result = rebuild_json(flat_elements, structure)
        
        if not result:
            print("   ⚠️ 재구성 실패, 원본 반환")
            return raw_data
        
        print("   → JSON 재구성 완료")
        
        # Step 4: Enrich (기존 Agent들로 세부 속성 설정)
        if not skip_enrich:
            if parallel:
                print(f"\n✨ Step 4: Enrich (병렬 처리, 동시 {self.max_concurrent}개)")
                result = asyncio.run(self._enrich_parallel(result))
            else:
                print("\n✨ Step 4: Enrich (순차 처리)")
                result = self._enrich_all_nodes(result)
            print("   → 세부 속성 설정 완료")
        else:
            print("\n⏭️ Step 4: Enrich 스킵")
        
        return result
    
    def _enrich_all_nodes(self, node: Dict, parent: Optional[Dict] = None, 
                          depth: int = 0) -> Dict:
        """
        모든 노드를 순회하며 세부 속성 설정 (멀티모달)
        각 Agent별로 이미지를 보고 판단
        """
        children = node.get('children', [])
        siblings = parent.get('children', []) if parent else []
        
        # 컨테이너 노드
        if children:
            print(f"   {'  ' * depth}📦 {node.get('id', '')[:20]}... ({node.get('type', '')})")
            
            # 1. Resizing (멀티모달)
            resizing_result = self._call_resizing_agent(node, parent, siblings, children)
            node['resizing'] = resizing_result.get('resizing', 'fill * fill')
            
            # 2. Layout (멀티모달)
            layout_result = self._call_layout_agent(node, parent, siblings, children)
            node['direction'] = layout_result.get('direction', 'vertical')
            node['gap'] = layout_result.get('gap', 10)
            node['padding'] = layout_result.get('padding', {'top': 0, 'right': 0, 'bottom': 0, 'left': 0})
            
            # 3. Alignment (멀티모달)
            alignment_result = self._call_alignment_agent(node, parent, siblings, children)
            node['alignment'] = alignment_result.get('alignment', 'leading')
            node['verticalAlignment'] = alignment_result.get('verticalAlignment', 'top')
            node['horizontalAlignment'] = alignment_result.get('horizontalAlignment', 'left')
            
            # 자식들 재귀 처리
            for child in children:
                self._enrich_all_nodes(child, node, depth + 1)
        
        else:
            # Leaf 노드
            print(f"   {'  ' * depth}📄 {node.get('id', '')[:20]}... ({node.get('type', '')})")
            
            resizing_result = self._call_resizing_agent(node, parent, siblings, [])
            node['resizing'] = resizing_result.get('resizing', 'fill * fill')
        
        return node
    
    # =========================================================================
    # 멀티모달 Agent 호출 (YAML 프롬프트 사용)
    # =========================================================================
    
    def _call_multimodal_llm(self, prompt_type: str, user_prompt: str) -> str:
        """멀티모달 LLM 호출 (YAML 프롬프트 사용)"""
        if not self.llm_client:
            return '{}'
        
        # YAML에서 시스템 프롬프트 로드
        system_prompt = self.prompt_loader._prompts.get(prompt_type, {}).get('system_role', '')
        config = self.prompt_loader.get_llm_config(prompt_type)
        
        if self.reference_image_base64:
            mime_type = get_image_mime_type(self.reference_image_path)
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{self.reference_image_base64}",
                        "detail": "high"
                    }
                },
                {"type": "text", "text": user_prompt}
            ]
        else:
            user_content = user_prompt
        
        response = self.llm_client.chat.completions.create(
            model=config.get('model', 'gpt-4o'),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=config.get('temperature', 0.2),
            max_tokens=config.get('max_tokens', 300)
        )
        return response.choices[0].message.content
    
    def _call_resizing_agent(self, node: Dict, parent: Optional[Dict],
                              siblings: List[Dict], children: List[Dict]) -> Dict:
        """Resizing Agent 호출 (멀티모달 + YAML 프롬프트)"""
        # 컨텍스트 준비
        context = {
            'node_info': self._get_node_summary(node, parent, children),
            'output_format': self.prompt_loader._prompts.get('resizing', {}).get('output_format', '')
        }
        
        # YAML에서 프롬프트 생성
        user_prompt = self.prompt_loader.get_prompt('resizing', context)
        
        try:
            response = self._call_multimodal_llm('resizing', user_prompt)
            return self._parse_json_response(response)
        except:
            return self._get_default_resizing(node)
    
    def _call_layout_agent(self, node: Dict, parent: Optional[Dict],
                            siblings: List[Dict], children: List[Dict]) -> Dict:
        """Layout Agent 호출 (멀티모달 + YAML 프롬프트)"""
        context = {
            'node_info': self._get_node_summary(node, parent, children),
            'output_format': self.prompt_loader._prompts.get('layout', {}).get('output_format', '')
        }
        
        user_prompt = self.prompt_loader.get_prompt('layout', context)
        
        try:
            response = self._call_multimodal_llm('layout', user_prompt)
            return self._parse_json_response(response)
        except:
            return self._get_default_layout(node)
    
    def _call_alignment_agent(self, node: Dict, parent: Optional[Dict],
                               siblings: List[Dict], children: List[Dict]) -> Dict:
        """Alignment Agent 호출 (멀티모달 + YAML 프롬프트)"""
        context = {
            'node_info': self._get_node_summary(node, parent, children),
            'output_format': self.prompt_loader._prompts.get('alignment', {}).get('output_format', '')
        }
        
        user_prompt = self.prompt_loader.get_prompt('alignment', context)
        
        try:
            response = self._call_multimodal_llm('alignment', user_prompt)
            return self._parse_json_response(response)
        except:
            return {'alignment': 'leading', 'verticalAlignment': 'top', 'horizontalAlignment': 'left'}
    
    def _get_node_summary(self, node: Dict, parent: Optional[Dict], children: List[Dict]) -> str:
        """노드 정보 요약"""
        info = f"""### 현재 노드:
- id: {node.get('id', '')[:20]}
- type: {node.get('type', '')}
- role: {node.get('role', '')}
- position: {node.get('position', {})}"""
        
        if parent:
            info += f"""

### 부모:
- type: {parent.get('type', '')}
- role: {parent.get('role', '')}"""
        
        if children:
            info += f"""

### 자식 ({len(children)}개):"""
            for c in children[:3]:
                info += f"\n- {c.get('type', '')} ({c.get('role', '')})"
        
        return info
    
    def _parse_json_response(self, response: str) -> Dict:
        """JSON 응답 파싱"""
        try:
            if '```json' in response:
                json_start = response.find('```json') + 7
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            elif '```' in response:
                json_start = response.find('```') + 3
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            else:
                json_str = response.strip()
            return json.loads(json_str)
        except:
            return {}
    
    def _get_default_resizing(self, node: Dict) -> Dict:
        """기본 resizing"""
        role = node.get('role', '')
        if 'Background' in role:
            return {'resizing': 'fill * fill'}
        elif 'Decoration' in role or 'Marker' in role or 'Separator' in role:
            return {'resizing': 'hug * hug'}
        elif 'Title' in role or 'Description' in role:
            return {'resizing': 'fill * hug'}
        return {'resizing': 'fill * fill'}
    
    def _get_default_layout(self, node: Dict) -> Dict:
        """기본 layout"""
        direction = 'horizontal' if node.get('type') == 'HStack' else 'vertical'
        return {
            'direction': direction,
            'gap': 10,
            'padding': {'top': 0, 'right': 0, 'bottom': 0, 'left': 0}
        }
    
    # =========================================================================
    # 병렬 처리
    # =========================================================================
    
    async def _enrich_parallel(self, root: Dict) -> Dict:
        """
        병렬 처리로 모든 노드 enrich
        depth별로 그룹화하여 같은 depth는 동시 처리
        """
        # 1. 모든 노드를 depth별로 그룹화
        depth_groups = self._group_nodes_by_depth(root)
        print(f"   → {len(depth_groups)}개 depth 레벨")
        
        # 2. 각 depth를 순차적으로, 같은 depth 내에서는 병렬로 처리
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        for depth, nodes_info in sorted(depth_groups.items()):
            print(f"   Depth {depth}: {len(nodes_info)}개 노드 처리 중...")
            
            tasks = []
            for node_info in nodes_info:
                task = self._enrich_single_node_async(
                    node_info['node'],
                    node_info['parent'],
                    node_info['siblings'],
                    semaphore
                )
                tasks.append(task)
            
            # 같은 depth의 노드들을 병렬로 처리
            await asyncio.gather(*tasks)
        
        return root
    
    def _group_nodes_by_depth(self, root: Dict) -> Dict[int, List[Dict]]:
        """노드들을 depth별로 그룹화"""
        groups = {}
        
        def traverse(node: Dict, parent: Optional[Dict], depth: int):
            siblings = parent.get('children', []) if parent else []
            
            if depth not in groups:
                groups[depth] = []
            
            groups[depth].append({
                'node': node,
                'parent': parent,
                'siblings': siblings
            })
            
            for child in node.get('children', []):
                traverse(child, node, depth + 1)
        
        traverse(root, None, 0)
        return groups
    
    async def _enrich_single_node_async(self, node: Dict, parent: Optional[Dict],
                                        siblings: List[Dict], semaphore: asyncio.Semaphore):
        """단일 노드를 비동기로 enrich (각 Agent 병렬 호출)"""
        async with semaphore:
            loop = asyncio.get_event_loop()
            children = node.get('children', [])
            
            # 1. Resizing
            try:
                resizing_result = await loop.run_in_executor(
                    None, lambda: self._call_resizing_agent(node, parent, siblings, children)
                )
                node['resizing'] = resizing_result.get('resizing', 'fill * fill')
            except:
                node['resizing'] = self._get_default_resizing(node).get('resizing', 'fill * fill')
            
            if children:
                # 2. Layout
                try:
                    layout_result = await loop.run_in_executor(
                        None, lambda: self._call_layout_agent(node, parent, siblings, children)
                    )
                    node['direction'] = layout_result.get('direction', 'vertical')
                    node['gap'] = layout_result.get('gap', 10)
                    node['padding'] = layout_result.get('padding', {'top': 0, 'right': 0, 'bottom': 0, 'left': 0})
                except:
                    defaults = self._get_default_layout(node)
                    node['direction'] = defaults['direction']
                    node['gap'] = defaults['gap']
                    node['padding'] = defaults['padding']
                
                # 3. Alignment
                try:
                    alignment_result = await loop.run_in_executor(
                        None, lambda: self._call_alignment_agent(node, parent, siblings, children)
                    )
                    node['alignment'] = alignment_result.get('alignment', 'leading')
                    node['verticalAlignment'] = alignment_result.get('verticalAlignment', 'top')
                    node['horizontalAlignment'] = alignment_result.get('horizontalAlignment', 'left')
                except:
                    node['alignment'] = 'leading'
                    node['verticalAlignment'] = 'top'
                    node['horizontalAlignment'] = 'left'


# =============================================================================
# CLI
# =============================================================================

def main():
    """메인 실행 함수"""
    import sys
    import os
    
    # 도움말
    if '--help' in sys.argv or '-h' in sys.argv:
        print("""
Structure Redesigner: Flatten → Design → Rebuild → Enrich

사용법:
  python structure_redesigner.py [옵션]

옵션:
  --image <경로>      참조 이미지 경로 (멀티모달 분석용)
  --input <경로>      입력 JSON 경로 (기본: data/raw_data.json)
  --output <경로>     출력 JSON 경로 (기본: data/redesigned_output.json)
  --skip-enrich       Enrich 단계 스킵 (구조만 재설계)
  --parallel          병렬 처리 사용 (Enrich 단계)
  --concurrent <N>    최대 동시 요청 수 (기본: 10)
  --help, -h          도움말 표시

예시:
  python structure_redesigner.py --image data/objects.png
  python structure_redesigner.py --image data/objects.png --parallel
  python structure_redesigner.py --image data/objects.png --parallel --concurrent 5
  python structure_redesigner.py --image data/objects.png --skip-enrich
""")
        return
    
    # 파일 경로
    raw_data_path = 'data/raw_data.json'
    output_path = 'data/redesigned_output.json'
    image_path = None
    skip_enrich = '--skip-enrich' in sys.argv
    use_parallel = '--parallel' in sys.argv
    max_concurrent = 10
    
    # CLI 옵션 파싱
    if '--image' in sys.argv:
        idx = sys.argv.index('--image')
        if idx + 1 < len(sys.argv):
            image_path = sys.argv[idx + 1]
    
    if '--input' in sys.argv:
        idx = sys.argv.index('--input')
        if idx + 1 < len(sys.argv):
            raw_data_path = sys.argv[idx + 1]
    
    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            output_path = sys.argv[idx + 1]
    
    if '--concurrent' in sys.argv:
        idx = sys.argv.index('--concurrent')
        if idx + 1 < len(sys.argv):
            try:
                max_concurrent = int(sys.argv[idx + 1])
            except ValueError:
                pass
    
    # raw_data.json 확인
    if not os.path.exists(raw_data_path):
        print(f"❌ 입력 파일을 찾을 수 없습니다: {raw_data_path}")
        return
    
    # LLM 클라이언트 설정
    llm_client = None
    try:
        from openai import OpenAI
        api_key = os.getenv('OPENAI_API_KEY')
        if api_key:
            llm_client = OpenAI(api_key=api_key)
            print("✅ OpenAI 클라이언트 초기화 완료")
        else:
            print("❌ OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
            return
    except ImportError:
        print("❌ openai 패키지가 설치되지 않았습니다.")
        return
    
    # 설정 출력
    print("\n📋 설정:")
    print(f"   - 입력: {raw_data_path}")
    print(f"   - 출력: {output_path}")
    print(f"   - 이미지: {image_path or '없음'}")
    print(f"   - Enrich: {'스킵' if skip_enrich else '실행'}")
    print(f"   - 병렬 처리: {'✅ 활성화 (동시 ' + str(max_concurrent) + '개)' if use_parallel else '❌ 비활성화'}")
    
    # 데이터 로드
    with open(raw_data_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
    
    import time
    start_time = time.time()
    
    # 재설계 실행
    redesigner = StructureRedesigner(llm_client, image_path, max_concurrent=max_concurrent)
    result = redesigner.redesign(raw_data, skip_enrich=skip_enrich, parallel=use_parallel)
    
    elapsed = time.time() - start_time
    
    # 결과 저장
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 결과 저장: {output_path}")
    print(f"⏱️ 소요 시간: {elapsed:.1f}초")


if __name__ == '__main__':
    main()
