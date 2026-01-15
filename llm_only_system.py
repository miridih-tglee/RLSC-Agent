"""
LLM 전용 멀티 에이전트 시스템
규칙 기반 로직을 제거하고 LLM만 사용하여 처리
병렬 처리 지원
멀티모달 (이미지+JSON) 분석 지원
"""

import json
import asyncio
import base64
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from copy import deepcopy


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
    path = Path(image_path).suffix.lower()
    mime_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    return mime_types.get(path, 'image/png')
from json_utils import (
    prepare_llm_context, 
    find_node_by_id, 
    extract_subtree,
    load_json_partial
)
from prompt_loader import PromptLoader


class LLMRuleAnalyzerAgent:
    """
    Agent 1: LLM 기반 Rule Analyzer
    - LLM을 사용하여 각 노드에 적용할 resizing 규칙을 결정
    - 규칙 파일 대신 LLM이 컨텍스트를 분석하여 결정
    """
    
    def __init__(self, llm_client, prompt_loader: Optional[PromptLoader] = None):
        """
        Args:
            llm_client: LLM 클라이언트 (OpenAI, Anthropic 등)
            prompt_loader: 프롬프트 로더 (None이면 자동 생성)
        """
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader or PromptLoader()
    
    def determine_resizing(self, node: Dict, parent: Optional[Dict] = None,
                          siblings: List[Dict] = None, 
                          context_nodes: List[Dict] = None,
                          is_root: bool = False) -> Dict:
        """
        LLM을 사용하여 노드의 resizing 규칙 결정
        
        Args:
            node: 현재 노드
            parent: 부모 노드
            siblings: 형제 노드들
            context_nodes: 주변 컨텍스트 노드들
            is_root: 최상위 블록 여부
        
        Returns:
            resizing 속성이 추가된 노드
        """
        if not self.llm_client:
            # LLM 없으면 기본값
            node['resizing'] = 'fill * fill'
            return node
        
        # LLM 프롬프트 생성
        prompt = self._create_resizing_prompt(node, parent, siblings, context_nodes, is_root)
        
        # LLM 호출
        response = self._call_llm(prompt)
        
        # 응답 파싱 및 적용
        resizing = self._parse_resizing_response(response)
        node['resizing'] = resizing
        
        return node
    
    def _create_resizing_prompt(self, node: Dict, parent: Optional[Dict],
                               siblings: List[Dict], context_nodes: List[Dict],
                               is_root: bool) -> str:
        """
        Resizing 규칙 결정을 위한 상세한 LLM 프롬프트 생성
        
        이 프롬프트는 매우 구체적으로 작성되어 LLM이 정확한 판단을 할 수 있도록 함
        """
        
        # 노드 정보 수집
        node_info = {
            'id': node.get('id'),
            'role': node.get('role', ''),
            'type': node.get('type', ''),
            'content': node.get('content', ''),
            'has_children': len(node.get('children', [])) > 0,
            'children_count': len(node.get('children', [])),
            'children_types': [c.get('type') for c in node.get('children', [])[:5]]
        }
        
        # 부모 정보
        parent_info = None
        if parent:
            parent_info = {
                'role': parent.get('role', ''),
                'type': parent.get('type', ''),
                'direction': parent.get('direction'),
                'resizing': parent.get('resizing', '')
            }
        
        # 형제 정보
        siblings_info = []
        if siblings:
            siblings_info = [
                {
                    'id': s.get('id'),
                    'role': s.get('role', ''),
                    'type': s.get('type', ''),
                    'position': 'right'  # 오른쪽 형제
                }
                for s in siblings[:3]
            ]
        
        # 컨텍스트 정보
        context_summary = {
            'total_nodes': len(context_nodes),
            'sample_nodes': context_nodes[:5] if context_nodes else []
        }
        
        # YAML 프롬프트 사용
        context = {
            'node_info': node_info,
            'parent_info': parent_info,
            'siblings_info': siblings_info,
            'context_summary': context_summary
        }
        prompt = self.prompt_loader.get_prompt('resizing', context)
        return prompt
    
    def _call_llm(self, prompt: str) -> str:
        """LLM 호출"""
        config = self.prompt_loader.get_llm_config('resizing')
        
        if hasattr(self.llm_client, 'chat'):
            # OpenAI 스타일
            system_message = self.prompt_loader._prompts['resizing'].get('system_role', '')
            response = self.llm_client.chat.completions.create(
                model=config.get('model', 'gpt-4'),
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                temperature=config.get('temperature', 0.2),
                max_tokens=config.get('max_tokens', 200)
            )
            return response.choices[0].message.content
        elif hasattr(self.llm_client, 'complete'):
            return self.llm_client.complete(prompt)
        else:
            return '{"resizing": "fill * fill", "reason": "기본값"}'
    
    def _parse_resizing_response(self, response: str) -> str:
        """LLM 응답에서 resizing 값 추출"""
        try:
            # JSON 추출
            if '```json' in response:
                json_start = response.find('```json') + 7
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            elif '```' in response:
                json_start = response.find('```') + 3
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            else:
                # JSON이 코드 블록 없이 있을 수도 있음
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                json_str = response[json_start:json_end]
            
            result = json.loads(json_str)
            resizing = result.get('resizing', 'fill * fill')
            
            # 유효성 검사
            if '*' not in resizing:
                return 'fill * fill'  # 기본값
            
            return resizing
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"⚠️ Resizing 파싱 오류: {e}")
            return 'fill * fill'  # 기본값


class LLMLayoutAnalyzerAgent:
    """
    Agent 2: LLM 기반 Layout Analyzer
    - LLM을 사용하여 direction, gap, padding 결정
    - 노드의 구조와 컨텍스트를 분석하여 최적의 레이아웃 속성 설정
    """
    
    def __init__(self, llm_client, prompt_loader: Optional[PromptLoader] = None):
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader or PromptLoader()
    
    def analyze_and_enrich(self, node: Dict, parent: Optional[Dict] = None) -> Dict:
        """
        LLM을 사용하여 레이아웃 속성 분석 및 추가
        
        Args:
            node: 현재 노드
            parent: 부모 노드
        
        Returns:
            레이아웃 속성이 추가된 노드
        """
        if not self.llm_client:
            # LLM 없으면 기본 로직
            return self._apply_default_layout(node)
        
        # LLM 프롬프트 생성
        prompt = self._create_layout_prompt(node, parent)
        
        # LLM 호출
        response = self._call_llm(prompt)
        
        # 응답 파싱 및 적용
        layout_props = self._parse_layout_response(response)
        self._apply_layout_properties(node, layout_props)
        
        return node
    
    def _create_layout_prompt(self, node: Dict, parent: Optional[Dict]) -> str:
        """레이아웃 분석을 위한 상세한 프롬프트 생성"""
        
        node_info = {
            'id': node.get('id'),
            'role': node.get('role', ''),
            'type': node.get('type', ''),
            'has_children': len(node.get('children', [])) > 0,
            'children_count': len(node.get('children', [])),
            'children_types': [c.get('type') for c in node.get('children', [])[:5]],
            'existing_gap': node.get('gap'),
            'existing_direction': node.get('direction'),
            'existing_padding': node.get('padding')
        }
        
        parent_info = None
        if parent:
            parent_info = {
                'role': parent.get('role', ''),
                'type': parent.get('type', ''),
                'direction': parent.get('direction', '')
            }
        
        # YAML 프롬프트 사용
        context = {
            'node_info': node_info,
            'parent_info': parent_info
        }
        prompt = self.prompt_loader.get_prompt('layout', context)
        return prompt
    
    def _call_llm(self, prompt: str) -> str:
        """LLM 호출"""
        config = self.prompt_loader.get_llm_config('layout')
        
        if hasattr(self.llm_client, 'chat'):
            system_message = self.prompt_loader._prompts['layout'].get('system_role', '')
            response = self.llm_client.chat.completions.create(
                model=config.get('model', 'gpt-4'),
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                temperature=config.get('temperature', 0.2),
                max_tokens=config.get('max_tokens', 300)
            )
            return response.choices[0].message.content
        elif hasattr(self.llm_client, 'complete'):
            return self.llm_client.complete(prompt)
        else:
            return '{"direction": "vertical", "gap": 10, "padding": {"top":0,"right":0,"bottom":0,"left":0}}'
    
    def _parse_layout_response(self, response: str) -> Dict:
        """LLM 응답 파싱"""
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
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                json_str = response[json_start:json_end]
            
            result = json.loads(json_str)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"⚠️ Layout 파싱 오류: {e}")
            return self._get_default_layout()
    
    def _apply_layout_properties(self, node: Dict, layout_props: Dict):
        """레이아웃 속성 적용"""
        if 'direction' in layout_props:
            node['direction'] = layout_props['direction']
        
        if 'gap' in layout_props and layout_props['gap'] is not None:
            if 'gap' not in node:  # 기존 값이 없을 때만
                node['gap'] = layout_props['gap']
        
        if 'horizontalGap' in layout_props and layout_props['horizontalGap'] is not None:
            node['horizontalGap'] = layout_props['horizontalGap']
        
        if 'verticalGap' in layout_props and layout_props['verticalGap'] is not None:
            node['verticalGap'] = layout_props['verticalGap']
        
        if 'padding' in layout_props:
            if 'padding' not in node:  # 기존 값이 없을 때만
                node['padding'] = layout_props['padding']
    
    def _apply_default_layout(self, node: Dict) -> Dict:
        """기본 레이아웃 적용 (LLM 없을 때)"""
        node_type = node.get('type', '')
        if node_type == 'HStack':
            node['direction'] = 'horizontal'
        elif node_type == 'VStack':
            node['direction'] = 'vertical'
        elif node_type == 'Group':
            node['direction'] = 'vertical'
        
        if 'children' in node and len(node.get('children', [])) > 1:
            if 'gap' not in node:
                node['gap'] = 10
            if node.get('direction') == 'horizontal':
                node['horizontalGap'] = node.get('gap', 10)
            elif node.get('direction') == 'vertical':
                node['verticalGap'] = node.get('gap', 10)
        
        if 'padding' not in node:
            node['padding'] = {'top': 0, 'right': 0, 'bottom': 0, 'left': 0}
        
        return node
    
    def _get_default_layout(self) -> Dict:
        """기본 레이아웃 값"""
        return {
            'direction': 'vertical',
            'gap': 10,
            'padding': {'top': 0, 'right': 0, 'bottom': 0, 'left': 0}
        }


class LLMAlignmentEnricherAgent:
    """
    Agent 3: LLM 기반 Alignment Enricher
    - LLM을 사용하여 alignment 속성 결정
    - 컨텍스트를 고려한 최적의 정렬 방식 설정
    """
    
    def __init__(self, llm_client, prompt_loader: Optional[PromptLoader] = None):
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader or PromptLoader()
    
    def enrich_alignments(self, node: Dict, parent: Optional[Dict] = None) -> Dict:
        """
        LLM을 사용하여 alignment 속성 추가
        
        Args:
            node: 현재 노드
            parent: 부모 노드
        
        Returns:
            alignment 속성이 추가된 노드
        """
        if not self.llm_client:
            return self._apply_default_alignment(node)
        
        # LLM 프롬프트 생성
        prompt = self._create_alignment_prompt(node, parent)
        
        # LLM 호출
        response = self._call_llm(prompt)
        
        # 응답 파싱 및 적용
        alignment_props = self._parse_alignment_response(response)
        self._apply_alignment_properties(node, alignment_props)
        
        return node
    
    def _create_alignment_prompt(self, node: Dict, parent: Optional[Dict]) -> str:
        """Alignment 분석을 위한 프롬프트 생성"""
        
        node_info = {
            'id': node.get('id'),
            'role': node.get('role', ''),
            'type': node.get('type', ''),
            'content': node.get('content', ''),
            'existing_alignment': node.get('alignment'),
            'direction': node.get('direction'),
            'has_children': len(node.get('children', [])) > 0
        }
        
        parent_info = None
        if parent:
            parent_info = {
                'role': parent.get('role', ''),
                'direction': parent.get('direction', ''),
                'alignment': parent.get('alignment', '')
            }
        
        # YAML 프롬프트 사용
        context = {
            'node_info': node_info,
            'parent_info': parent_info
        }
        prompt = self.prompt_loader.get_prompt('alignment', context)
        return prompt
    
    def _call_llm(self, prompt: str) -> str:
        """LLM 호출"""
        config = self.prompt_loader.get_llm_config('alignment')
        
        if hasattr(self.llm_client, 'chat'):
            system_message = self.prompt_loader._prompts['alignment'].get('system_role', '')
            response = self.llm_client.chat.completions.create(
                model=config.get('model', 'gpt-4'),
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                temperature=config.get('temperature', 0.2),
                max_tokens=config.get('max_tokens', 200)
            )
            return response.choices[0].message.content
        elif hasattr(self.llm_client, 'complete'):
            return self.llm_client.complete(prompt)
        else:
            return '{"alignment": "center", "verticalAlignment": "center", "horizontalAlignment": "center"}'
    
    def _parse_alignment_response(self, response: str) -> Dict:
        """LLM 응답 파싱"""
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
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                json_str = response[json_start:json_end]
            
            result = json.loads(json_str)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"⚠️ Alignment 파싱 오류: {e}")
            return self._get_default_alignment()
    
    def _apply_alignment_properties(self, node: Dict, alignment_props: Dict):
        """Alignment 속성 적용"""
        if 'alignment' in alignment_props:
            node['alignment'] = alignment_props['alignment']
        if 'verticalAlignment' in alignment_props:
            node['verticalAlignment'] = alignment_props['verticalAlignment']
        if 'horizontalAlignment' in alignment_props:
            node['horizontalAlignment'] = alignment_props['horizontalAlignment']
    
    def _apply_default_alignment(self, node: Dict) -> Dict:
        """기본 alignment 적용"""
        existing = node.get('alignment')
        if existing == 'center':
            node['alignment'] = 'center'
            node['verticalAlignment'] = 'center'
            node['horizontalAlignment'] = 'center'
        elif existing == 'leading':
            node['alignment'] = 'leading'
            node['horizontalAlignment'] = 'left'
            node['verticalAlignment'] = 'center'
        elif existing == 'trailing':
            node['alignment'] = 'trailing'
            node['horizontalAlignment'] = 'right'
            node['verticalAlignment'] = 'center'
        else:
            node['alignment'] = 'center'
            node['verticalAlignment'] = 'center'
            node['horizontalAlignment'] = 'center'
        return node
    
    def _get_default_alignment(self) -> Dict:
        """기본 alignment 값"""
        return {
            'alignment': 'center',
            'verticalAlignment': 'center',
            'horizontalAlignment': 'center'
        }


class LLMRoleValidatorAgent:
    """
    Agent 4: LLM 기반 Role Validator (멀티모달 지원)
    - LLM을 사용하여 각 노드의 Role이 올바르게 할당되었는지 검증
    - 이미지를 함께 분석하여 시각적 의미 파악
    - 계층 구조, 제약 조건, 의미론적 일관성 검사
    - 문제 발견 시 자동 수정 (Role 변경, Group으로 묶기 등)
    """
    
    def __init__(self, llm_client, prompt_loader: Optional[PromptLoader] = None,
                 reference_image_path: Optional[str] = None):
        """
        Args:
            llm_client: LLM 클라이언트 (OpenAI, Anthropic 등)
            prompt_loader: 프롬프트 로더 (None이면 자동 생성)
            reference_image_path: 참조 이미지 경로 (멀티모달 분석용)
        """
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader or PromptLoader()
        self.pending_structure_changes = []  # 구조 변경 대기열
        
        # 멀티모달 설정
        self.reference_image_path = reference_image_path
        self.reference_image_base64 = None
        if reference_image_path:
            self.reference_image_base64 = encode_image_to_base64(reference_image_path)
            if self.reference_image_base64:
                print(f"📷 참조 이미지 로드 완료: {reference_image_path}")
    
    def validate_role(self, node: Dict, parent: Optional[Dict] = None,
                     siblings: List[Dict] = None,
                     children: List[Dict] = None) -> Dict:
        """
        LLM을 사용하여 노드의 Role 검증
        
        Args:
            node: 현재 노드
            parent: 부모 노드
            siblings: 형제 노드들
            children: 자식 노드들
        
        Returns:
            검증 결과 딕셔너리
        """
        if not self.llm_client:
            # LLM 없으면 기본 검증 결과
            return {
                'is_valid': True,
                'current_role': node.get('role', ''),
                'issues': [],
                'suggestions': [],
                'confidence': 0.5,
                'reason': 'LLM 없이 기본 검증만 수행'
            }
        
        # LLM 프롬프트 생성
        prompt = self._create_validation_prompt(node, parent, siblings, children)
        
        # LLM 호출
        response = self._call_llm(prompt)
        
        # 응답 파싱
        result = self._parse_validation_response(response)
        
        return result
    
    def validate_and_fix(self, node: Dict, parent: Optional[Dict] = None,
                        siblings: List[Dict] = None,
                        children: List[Dict] = None) -> Tuple[Dict, bool]:
        """
        Role 검증 후 문제가 있으면 자동 수정
        
        Args:
            node: 현재 노드
            parent: 부모 노드
            siblings: 형제 노드들
            children: 자식 노드들
        
        Returns:
            (검증 결과, 수정 여부) 튜플
        """
        result = self.validate_role(node, parent, siblings, children)
        
        modified = False
        
        # ⭐ 먼저: 자식 그룹 안에 있는 "전체 덮는 Background"를 승격
        if children:
            bg_promoted = self._promote_full_coverage_background(node)
            if bg_promoted:
                modified = True
                # children 목록 갱신
                children = node.get('children', [])
        
        if not result.get('is_valid', True):
            # 수정 제안이 있으면 적용
            for suggestion in result.get('suggestions', []):
                action = suggestion.get('action')
                
                if action == 'change_role':
                    # Role 변경
                    target_id = suggestion.get('target_id')
                    suggested_role = suggestion.get('suggested_role')
                    
                    if target_id == node.get('id') and suggested_role:
                        old_role = node.get('role', '')
                        node['role'] = suggested_role
                        modified = True
                        print(f"      🔧 Role 수정: {old_role} → {suggested_role}")
                    
                    # 자식 노드의 Role 수정
                    elif children and suggested_role:
                        for child in children:
                            if child.get('id') == target_id:
                                old_role = child.get('role', '')
                                child['role'] = suggested_role
                                modified = True
                                print(f"      🔧 자식 Role 수정: {target_id} ({old_role} → {suggested_role})")
                                break
                
                elif action == 'wrap_with_group':
                    # Group으로 묶기 - 이슈 타입에 따라 다르게 처리
                    target_ids = suggestion.get('target_ids', [])
                    issue_type = suggestion.get('issue_type', '')
                    
                    if target_ids and children:
                        # Background 중복 문제: 전체 구조 재편성
                        if issue_type == 'background_duplicate':
                            new_group_role = suggestion.get('new_group_role', 'Role.LayoutContainer.Decoration')
                            success = self._wrap_children_with_group(node, children, target_ids, new_group_role, suggestion)
                            if success:
                                modified = True
                                print(f"      🔧 Background 중복 해결: Group으로 묶음")
                        # Decoration 겹침 문제: Decoration만 묶기
                        else:
                            success = self._wrap_decorations_only(node, target_ids, suggestion)
                            if success:
                                modified = True
                
                elif action == 'restructure_overlapping':
                    # 겹치는 Decoration만 구조 재편성 (Background는 건드리지 않음)
                    if children:
                        success = self._restructure_overlapping_elements(node, children, suggestion)
                        if success:
                            modified = True
        
        # 검증 메타데이터 추가
        node['_role_validation'] = {
            'is_valid': result.get('is_valid', True),
            'confidence': result.get('confidence', 0.0),
            'issues_count': len(result.get('issues', []))
        }
        
        return result, modified
    
    def _promote_full_coverage_background(self, node: Dict) -> bool:
        """
        자식 Group 안에 있는 "전체를 덮는 Background"를 현재 노드 레벨로 승격
        
        예: group_card1 안의 group_card1_icon 안에 있는 큰 흰색 배경 →
            group_card1의 직접 자식으로 이동
        
        규칙:
        - 자식 Group 안의 Background가
        - 부모 노드의 90% 이상을 차지하면
        - 해당 Background를 부모의 직접 자식으로 승격
        """
        children = node.get('children', [])
        if not children:
            return False
        
        node_pos = node.get('position', {})
        node_area = node_pos.get('width', 0) * node_pos.get('height', 0)
        if node_area == 0:
            return False
        
        modified = False
        
        for child in children:
            # Group/VStack/HStack 타입의 자식만 검사
            if child.get('type') not in ['Group', 'VStack', 'HStack']:
                continue
            
            child_children = child.get('children', [])
            if not child_children:
                continue
            
            # 자식 그룹 안에서 Background 찾기
            bg_to_promote = None
            bg_index = -1
            
            for i, grandchild in enumerate(child_children):
                if 'Background' in grandchild.get('role', ''):
                    gc_pos = grandchild.get('position', {})
                    gc_area = gc_pos.get('width', 0) * gc_pos.get('height', 0)
                    
                    # 부모 노드의 70% 이상을 차지하면 승격 대상
                    if gc_area >= node_area * 0.7:
                        bg_to_promote = grandchild
                        bg_index = i
                        break
            
            if bg_to_promote and bg_index >= 0:
                # 자식 그룹에서 Background 제거
                child_children.pop(bg_index)
                
                # Background의 role 확인 및 설정
                bg_to_promote['role'] = 'Role.Element.Background'
                
                # 현재 노드의 첫 번째 자식으로 삽입 (배경이므로 맨 뒤에 렌더링)
                children.insert(0, bg_to_promote)
                
                print(f"      ⬆️ Background 승격: {bg_to_promote.get('id', '')[:8]}...")
                print(f"         {child.get('id', '')} → {node.get('id', '')} 레벨로 이동")
                
                modified = True
        
        return modified
    
    def _wrap_children_with_group(self, parent_node: Dict, children: List[Dict], 
                                   target_ids: List[str], new_group_role: str,
                                   suggestion: Dict) -> bool:
        """
        지정된 자식들을 새 Group으로 묶기 (중첩 겹침도 재귀 처리)
        
        Args:
            parent_node: 부모 노드
            children: 현재 자식 노드들 (부모의 children 참조)
            target_ids: 묶을 노드 ID 리스트
            new_group_role: 새 Group의 Role
            suggestion: LLM 제안 (추가 정보 포함)
        
        Returns:
            성공 여부
        """
        import uuid
        
        # 묶을 노드들 찾기
        nodes_to_wrap = []
        indices_to_remove = []
        
        parent_children = parent_node.get('children', [])
        
        for i, child in enumerate(parent_children):
            if child.get('id') in target_ids:
                nodes_to_wrap.append(child)
                indices_to_remove.append(i)
        
        if len(nodes_to_wrap) < 2:
            return False  # 묶을 노드가 2개 미만이면 스킵
        
        # 겹침 그룹 분석 및 중첩 구조 생성
        structured_children = self._create_nested_overlap_structure(nodes_to_wrap)
        
        # position 계산 (묶이는 노드들의 bounding box)
        positions = [n.get('position', {}) for n in nodes_to_wrap if n.get('position')]
        min_x, min_y, group_width, group_height = 0, 0, 0, 0
        if positions:
            min_x = min(p.get('x', 0) for p in positions)
            min_y = min(p.get('y', 0) for p in positions)
            max_x = max(p.get('x', 0) + p.get('width', 0) for p in positions)
            max_y = max(p.get('y', 0) + p.get('height', 0) for p in positions)
            group_width = max_x - min_x
            group_height = max_y - min_y
        
        # 새 Group 생성 (필수 속성 포함)
        new_group = {
            'id': f"auto_group_{uuid.uuid4().hex[:8]}",
            'type': 'Group',
            'role': new_group_role,
            'children': structured_children,
            '_auto_generated': True,
            '_reason': suggestion.get('reason', 'LLM 제안에 의한 자동 그룹화'),
            'position': {
                'x': min_x,
                'y': min_y,
                'width': group_width,
                'height': group_height
            },
            # 필수 속성들 추가
            'resizing': 'hug * hug',
            'direction': 'vertical',
            'alignment': 'center',
            'verticalAlignment': 'center',
            'horizontalAlignment': 'center',
            'padding': {
                'top': 0,
                'right': 0,
                'bottom': 0,
                'left': 0
            },
            'gap': 0
        }
        
        # 기존 children에서 제거 (역순으로 제거해야 인덱스 꼬이지 않음)
        for idx in sorted(indices_to_remove, reverse=True):
            parent_children.pop(idx)
        
        # 새 Group을 children의 처음에 삽입 (배경 레이어니까)
        parent_children.insert(0, new_group)
        
        return True
    
    def _create_nested_overlap_structure(self, nodes: List[Dict]) -> List[Dict]:
        """
        겹치는 노드들을 중첩 구조로 변환
        
        알고리즘:
        1. 면적 순 정렬 (큰 것 = 뒤, 작은 것 = 앞)
        2. 가장 큰 것을 Background로
        3. 나머지 중 서로 겹치는 것들끼리 또 Group으로 묶기 (재귀)
        4. 안 겹치는 것들은 Decoration으로 그대로 추가
        
        Returns:
            중첩 구조가 적용된 children 리스트
        """
        import uuid
        
        if len(nodes) < 2:
            return nodes
        
        def get_area(n):
            pos = n.get('position', {})
            return pos.get('width', 0) * pos.get('height', 0)
        
        def boxes_overlap(a: Dict, b: Dict) -> bool:
            """두 position이 겹치는지 확인"""
            if not a or not b:
                return False
            a_x1, a_y1 = a.get('x', 0), a.get('y', 0)
            a_x2, a_y2 = a_x1 + a.get('width', 0), a_y1 + a.get('height', 0)
            b_x1, b_y1 = b.get('x', 0), b.get('y', 0)
            b_x2, b_y2 = b_x1 + b.get('width', 0), b_y1 + b.get('height', 0)
            return not (a_x2 <= b_x1 or b_x2 <= a_x1 or a_y2 <= b_y1 or b_y2 <= a_y1)
        
        # 면적 순 정렬 (큰 것부터)
        sorted_nodes = sorted(nodes, key=get_area, reverse=True)
        
        # 가장 큰 것을 Background로 설정
        background_node = sorted_nodes[0]
        if background_node.get('role', '').startswith('Role.Element'):
            background_node['role'] = 'Role.Element.Background'
        
        remaining_nodes = sorted_nodes[1:]
        
        if not remaining_nodes:
            return [background_node]
        
        # 나머지 노드들 중 겹치는 것들 그룹화
        result = [background_node]
        
        # 각 노드가 다른 노드와 겹치는지 확인하여 그룹화
        processed = set()
        
        for i, node in enumerate(remaining_nodes):
            if node.get('id') in processed:
                continue
            
            # 이 노드와 겹치는 다른 노드들 찾기
            overlapping = [node]
            for j, other in enumerate(remaining_nodes):
                if i != j and other.get('id') not in processed:
                    if boxes_overlap(node.get('position'), other.get('position')):
                        overlapping.append(other)
                        processed.add(other.get('id'))
            
            processed.add(node.get('id'))
            
            if len(overlapping) > 1:
                # 겹치는 것들끼리 중첩 Group 생성 (재귀!)
                nested_children = self._create_nested_overlap_structure(overlapping)
                
                # position 계산
                positions = [n.get('position', {}) for n in overlapping if n.get('position')]
                min_x, min_y, group_width, group_height = 0, 0, 0, 0
                if positions:
                    min_x = min(p.get('x', 0) for p in positions)
                    min_y = min(p.get('y', 0) for p in positions)
                    max_x = max(p.get('x', 0) + p.get('width', 0) for p in positions)
                    max_y = max(p.get('y', 0) + p.get('height', 0) for p in positions)
                    group_width = max_x - min_x
                    group_height = max_y - min_y
                
                # 새 중첩 Group (필수 속성 포함)
                nested_group = {
                    'id': f"auto_nested_{uuid.uuid4().hex[:8]}",
                    'type': 'Group',
                    'role': 'Role.LayoutContainer.Decoration',
                    'children': nested_children,
                    '_auto_generated': True,
                    '_reason': '중첩 겹침에 의한 자동 그룹화',
                    'position': {
                        'x': min_x,
                        'y': min_y,
                        'width': group_width,
                        'height': group_height
                    },
                    # 필수 속성들 추가
                    'resizing': 'hug * hug',
                    'direction': 'vertical',
                    'alignment': 'center',
                    'verticalAlignment': 'center',
                    'horizontalAlignment': 'center',
                    'padding': {
                        'top': 0,
                        'right': 0,
                        'bottom': 0,
                        'left': 0
                    },
                    'gap': 0
                }
                
                result.append(nested_group)
            else:
                # 안 겹치는 단일 노드는 Decoration으로
                if 'Background' in node.get('role', ''):
                    node['role'] = 'Role.Element.Decoration'
                result.append(node)
        
        return result
    
    def _restructure_overlapping_elements(self, parent_node: Dict, children: List[Dict],
                                          suggestion: Dict) -> bool:
        """
        겹치는 요소들의 구조를 자동으로 재편성
        
        핵심 규칙:
        - Background는 1개뿐이면 건드리지 않음
        - Decoration끼리 겹치는 것만 Group으로 묶기
        - 안 겹치는 요소들은 원래 위치 유지
        """
        parent_children = parent_node.get('children', [])
        if len(parent_children) < 2:
            return False
        
        # Decoration들만 추출
        decorations = [
            n for n in parent_children 
            if 'Decoration' in n.get('role', '')
        ]
        
        if len(decorations) < 2:
            return False  # Decoration이 2개 미만이면 겹침 처리 불필요
        
        # Decoration들 사이에서만 겹침 감지
        overlapping_groups = self._detect_overlapping_groups_decorations_only(decorations)
        
        if not overlapping_groups:
            return False
        
        modified = False
        for group_ids in overlapping_groups:
            if len(group_ids) >= 2:
                success = self._wrap_decorations_only(
                    parent_node, 
                    group_ids,
                    {'reason': '겹치는 Decoration들을 Group으로 묶기'}
                )
                if success:
                    modified = True
        
        return modified
    
    def _detect_overlapping_groups_decorations_only(self, decorations: List[Dict]) -> List[List[str]]:
        """
        Decoration들 사이에서만 겹침 감지
        """
        def boxes_overlap(a: Dict, b: Dict) -> bool:
            if not a or not b:
                return False
            a_x1, a_y1 = a.get('x', 0), a.get('y', 0)
            a_x2, a_y2 = a_x1 + a.get('width', 0), a_y1 + a.get('height', 0)
            b_x1, b_y1 = b.get('x', 0), b.get('y', 0)
            b_x2, b_y2 = b_x1 + b.get('width', 0), b_y1 + b.get('height', 0)
            return not (a_x2 <= b_x1 or b_x2 <= a_x1 or a_y2 <= b_y1 or b_y2 <= a_y1)
        
        if len(decorations) < 2:
            return []
        
        # Union-Find
        parent_map = {n.get('id'): n.get('id') for n in decorations}
        
        def find(x):
            if parent_map[x] != x:
                parent_map[x] = find(parent_map[x])
            return parent_map[x]
        
        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent_map[px] = py
        
        # 겹치는 쌍 찾기
        for i in range(len(decorations)):
            for j in range(i + 1, len(decorations)):
                n1, n2 = decorations[i], decorations[j]
                if boxes_overlap(n1.get('position'), n2.get('position')):
                    union(n1.get('id'), n2.get('id'))
        
        # 그룹별로 모으기
        groups = {}
        for n in decorations:
            root = find(n.get('id'))
            if root not in groups:
                groups[root] = []
            groups[root].append(n.get('id'))
        
        # 2개 이상인 그룹만 반환
        return [ids for ids in groups.values() if len(ids) >= 2]
    
    def _wrap_decorations_only(self, parent_node: Dict, target_ids: List[str], 
                               suggestion: Dict) -> bool:
        """
        Decoration들만 Group으로 묶기
        
        핵심 규칙:
        - 원래 Role이 Background인 요소는 건드리지 않음!
        - 자식들의 position을 새 Group 기준 상대 좌표로 변환 (룰베이스)
        - raw_data가 부모 기준 상대 좌표이므로, 새 Group도 동일하게 처리
        """
        import uuid
        
        parent_children = parent_node.get('children', [])
        
        # 묶을 Decoration들 찾기 (실제로 Decoration인 것만!)
        nodes_to_wrap = []
        indices_to_remove = []
        
        for i, child in enumerate(parent_children):
            if child.get('id') in target_ids:
                # Background는 제외! Decoration만 묶음
                if 'Background' in child.get('role', ''):
                    continue
                nodes_to_wrap.append(child)
                indices_to_remove.append(i)
        
        if len(nodes_to_wrap) < 2:
            return False
        
        # 새 Group의 position 계산 (bounding box)
        positions = [n.get('position', {}) for n in nodes_to_wrap if n.get('position')]
        if not positions:
            return False
        
        # Group의 위치 = 자식들의 bounding box
        group_x = min(p.get('x', 0) for p in positions)
        group_y = min(p.get('y', 0) for p in positions)
        max_x = max(p.get('x', 0) + p.get('width', 0) for p in positions)
        max_y = max(p.get('y', 0) + p.get('height', 0) for p in positions)
        group_width = max_x - group_x
        group_height = max_y - group_y
        
        # ⭐ 룰베이스: 자식들의 position을 새 Group 기준 상대 좌표로 변환
        for node in nodes_to_wrap:
            pos = node.get('position', {})
            if pos:
                node['position'] = {
                    'x': pos.get('x', 0) - group_x,
                    'y': pos.get('y', 0) - group_y,
                    'width': pos.get('width', 0),
                    'height': pos.get('height', 0)
                }
        
        # 면적 순 정렬 (큰 것이 뒤에 = Background 역할)
        def get_area(n):
            pos = n.get('position', {})
            return pos.get('width', 0) * pos.get('height', 0)
        
        sorted_nodes = sorted(nodes_to_wrap, key=get_area, reverse=True)
        
        # 새 Group 안에서만 Background/Decoration 할당
        for i, node in enumerate(sorted_nodes):
            if i == 0:
                node['role'] = 'Role.Element.Background'
            else:
                node['role'] = 'Role.Element.Decoration'
        
        # 새 Group 생성 (필수 속성 포함)
        new_group = {
            'id': f"auto_deco_group_{uuid.uuid4().hex[:8]}",
            'type': 'Group',
            'role': 'Role.LayoutContainer.Decoration',
            'children': sorted_nodes,
            '_auto_generated': True,
            '_reason': suggestion.get('reason', '겹치는 Decoration 그룹화'),
            'position': {
                'x': group_x,
                'y': group_y,
                'width': group_width,
                'height': group_height
            },
            # 필수 속성들 추가
            'resizing': 'hug * hug',  # Decoration 그룹은 내용물 크기에 맞춤
            'direction': 'vertical',
            'alignment': 'center',
            'verticalAlignment': 'center',
            'horizontalAlignment': 'center',
            'padding': {
                'top': 0,
                'right': 0,
                'bottom': 0,
                'left': 0
            },
            'gap': 0
        }
        
        # 기존 children에서 제거 (역순)
        for idx in sorted(indices_to_remove, reverse=True):
            parent_children.pop(idx)
        
        # 새 Group 삽입 (원래 위치에)
        insert_idx = min(indices_to_remove) if indices_to_remove else 0
        parent_children.insert(insert_idx, new_group)
        
        print(f"         📦 Decoration 그룹화: {[n.get('id')[:8] for n in nodes_to_wrap]}")
        print(f"            Group position: ({group_x:.1f}, {group_y:.1f})")
        print(f"            자식 상대좌표 변환 완료")
        
        return True
    
    def _detect_overlapping_groups(self, nodes: List[Dict]) -> List[List[str]]:
        """
        겹치는 노드들을 그룹으로 묶어서 반환
        """
        def boxes_overlap(a: Dict, b: Dict) -> bool:
            """두 position이 겹치는지 확인"""
            if not a or not b:
                return False
            
            a_x1, a_y1 = a.get('x', 0), a.get('y', 0)
            a_x2, a_y2 = a_x1 + a.get('width', 0), a_y1 + a.get('height', 0)
            b_x1, b_y1 = b.get('x', 0), b.get('y', 0)
            b_x2, b_y2 = b_x1 + b.get('width', 0), b_y1 + b.get('height', 0)
            
            # 겹치지 않는 조건의 부정
            return not (a_x2 <= b_x1 or b_x2 <= a_x1 or a_y2 <= b_y1 or b_y2 <= a_y1)
        
        # Background나 Decoration만 필터링
        target_nodes = [
            n for n in nodes 
            if 'Background' in n.get('role', '') or 'Decoration' in n.get('role', '')
        ]
        
        if len(target_nodes) < 2:
            return []
        
        # Union-Find로 겹치는 그룹 찾기
        parent_map = {n.get('id'): n.get('id') for n in target_nodes}
        
        def find(x):
            if parent_map[x] != x:
                parent_map[x] = find(parent_map[x])
            return parent_map[x]
        
        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent_map[px] = py
        
        # 겹치는 쌍 찾기
        for i in range(len(target_nodes)):
            for j in range(i + 1, len(target_nodes)):
                n1, n2 = target_nodes[i], target_nodes[j]
                if boxes_overlap(n1.get('position'), n2.get('position')):
                    union(n1.get('id'), n2.get('id'))
        
        # 그룹별로 모으기
        groups = {}
        for n in target_nodes:
            root = find(n.get('id'))
            if root not in groups:
                groups[root] = []
            groups[root].append(n.get('id'))
        
        # 2개 이상인 그룹만 반환
        return [ids for ids in groups.values() if len(ids) >= 2]
    
    def _create_validation_prompt(self, node: Dict, parent: Optional[Dict],
                                  siblings: List[Dict], children: List[Dict]) -> str:
        """
        Role 검증을 위한 LLM 프롬프트 생성
        """
        # 노드 정보 수집 (position 포함!)
        node_info = {
            'id': node.get('id'),
            'role': node.get('role', ''),
            'type': node.get('type', ''),
            'content': node.get('content', '')[:200] if node.get('content') else '',
            'position': node.get('position'),  # 겹침 판단용!
            'has_children': len(node.get('children', [])) > 0,
            'children_count': len(node.get('children', []))
        }
        
        # 부모 정보
        parent_info = None
        if parent:
            parent_info = {
                'id': parent.get('id'),
                'role': parent.get('role', ''),
                'type': parent.get('type', '')
            }
        
        # 형제 정보 (같은 Role을 가진 형제 수 + position 포함)
        siblings_info = []
        same_role_count = 0
        if siblings:
            current_role = node.get('role', '')
            for s in siblings[:5]:  # 최대 5개
                sibling_role = s.get('role', '')
                if sibling_role == current_role:
                    same_role_count += 1
                siblings_info.append({
                    'id': s.get('id'),
                    'role': sibling_role,
                    'type': s.get('type', ''),
                    'position': s.get('position')  # 겹침 판단용!
                })
        
        siblings_summary = {
            'siblings': siblings_info,
            'same_role_count': same_role_count,
            'total_siblings': len(siblings) if siblings else 0
        }
        
        # 자식 노드 정보 (Role 분포 + position 포함 - 겹침 판단용!)
        children_info = []
        children_role_counts = {}
        if children:
            for c in children[:10]:  # 최대 10개
                child_role = c.get('role', '')
                children_role_counts[child_role] = children_role_counts.get(child_role, 0) + 1
                children_info.append({
                    'id': c.get('id'),
                    'role': child_role,
                    'type': c.get('type', ''),
                    'content': c.get('content', '')[:100] if c.get('content') else '',
                    'position': c.get('position')  # 겹침 판단용!
                })
        
        children_summary = {
            'children': children_info,
            'role_distribution': children_role_counts,
            'total_children': len(children) if children else 0
        }
        
        # 컨텍스트 구성
        context = {
            'node_info': node_info,
            'parent_info': parent_info,
            'siblings_info': siblings_summary,
            'children_info': children_summary
        }
        
        prompt = self.prompt_loader.get_prompt('role_validation', context)
        return prompt
    
    def _call_llm(self, prompt: str) -> str:
        """LLM 호출 (멀티모달 지원)"""
        config = self.prompt_loader.get_llm_config('role_validation')
        
        if hasattr(self.llm_client, 'chat'):
            # OpenAI 스타일
            system_message = self.prompt_loader._prompts.get('role_validation', {}).get('system_role', '')
            
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
            
            response = self.llm_client.chat.completions.create(
                model=config.get('model', 'gpt-4o'),  # 멀티모달은 gpt-4o 필요
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_content}
                ],
                temperature=config.get('temperature', 0.1),
                max_tokens=config.get('max_tokens', 500)
            )
            return response.choices[0].message.content
        elif hasattr(self.llm_client, 'complete'):
            return self.llm_client.complete(prompt)
        else:
            return '{"is_valid": true, "current_role": "", "issues": [], "suggestions": [], "confidence": 0.5, "reason": "기본 검증"}'
    
    def _parse_validation_response(self, response: str) -> Dict:
        """LLM 응답에서 검증 결과 추출"""
        try:
            # JSON 추출
            if '```json' in response:
                json_start = response.find('```json') + 7
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            elif '```' in response:
                json_start = response.find('```') + 3
                json_end = response.find('```', json_start)
                json_str = response[json_start:json_end].strip()
            else:
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                json_str = response[json_start:json_end]
            
            result = json.loads(json_str)
            
            # 필수 필드 확인 및 기본값 설정
            return {
                'is_valid': result.get('is_valid', True),
                'current_role': result.get('current_role', ''),
                'issues': result.get('issues', []),
                'suggestions': result.get('suggestions', []),
                'confidence': result.get('confidence', 0.5),
                'reason': result.get('reason', '')
            }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"⚠️ Role Validation 파싱 오류: {e}")
            return {
                'is_valid': True,
                'current_role': '',
                'issues': [],
                'suggestions': [],
                'confidence': 0.0,
                'reason': f'파싱 오류: {str(e)}'
            }
    
    def generate_validation_report(self, all_results: List[Dict]) -> Dict:
        """
        전체 검증 결과에 대한 리포트 생성
        
        Args:
            all_results: 모든 노드의 검증 결과 리스트
        
        Returns:
            종합 리포트
        """
        total_nodes = len(all_results)
        valid_nodes = sum(1 for r in all_results if r.get('is_valid', True))
        invalid_nodes = total_nodes - valid_nodes
        
        # 이슈 타입별 집계
        issue_types = {}
        all_issues = []
        for r in all_results:
            for issue in r.get('issues', []):
                issue_type = issue.get('type', 'unknown')
                issue_types[issue_type] = issue_types.get(issue_type, 0) + 1
                all_issues.append(issue)
        
        # 심각도별 집계
        severity_counts = {'error': 0, 'warning': 0, 'info': 0}
        for issue in all_issues:
            severity = issue.get('severity', 'info')
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        
        # 평균 신뢰도
        confidences = [r.get('confidence', 0.0) for r in all_results]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        
        return {
            'summary': {
                'total_nodes': total_nodes,
                'valid_nodes': valid_nodes,
                'invalid_nodes': invalid_nodes,
                'validation_rate': valid_nodes / total_nodes if total_nodes > 0 else 0.0,
                'average_confidence': avg_confidence
            },
            'issues_by_type': issue_types,
            'issues_by_severity': severity_counts,
            'total_issues': len(all_issues),
            'all_issues': all_issues[:20]  # 상위 20개만
        }


class LLMCoordinatorAgent:
    """
    Agent 5: LLM 기반 Coordinator (멀티모달 지원)
    - 전체 프로세스를 조율
    - 각 LLM 에이전트를 순차적으로 호출
    - 대용량 파일 처리를 위한 최적화
    - 참조 이미지를 통한 시각적 분석 지원
    """
    
    def __init__(self, llm_client, use_partial_loading: bool = False, 
                 prompt_loader: Optional[PromptLoader] = None,
                 enable_role_validation: bool = True,
                 reference_image_path: Optional[str] = None):
        """
        Args:
            llm_client: LLM 클라이언트
            use_partial_loading: 부분 로드 사용 여부
            prompt_loader: 프롬프트 로더 (None이면 자동 생성)
            enable_role_validation: Role 검증 활성화 여부
            reference_image_path: 참조 이미지 경로 (멀티모달 분석용)
        """
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader or PromptLoader()
        
        # 모든 에이전트에 prompt_loader 전달 (멀티모달 지원)
        self.role_validator = LLMRoleValidatorAgent(
            llm_client, self.prompt_loader, reference_image_path
        )
        self.rule_analyzer = LLMRuleAnalyzerAgent(llm_client, self.prompt_loader)
        self.layout_analyzer = LLMLayoutAnalyzerAgent(llm_client, self.prompt_loader)
        self.alignment_enricher = LLMAlignmentEnricherAgent(llm_client, self.prompt_loader)
        
        self.use_partial_loading = use_partial_loading
        self.enable_role_validation = enable_role_validation
        self.validation_results = []  # 검증 결과 수집용
        self.reference_image_path = reference_image_path
    
    def process(self, raw_data: Dict, simplified_structure: Dict,
                target_id: Optional[str] = None) -> Dict:
        """
        전체 프로세스 실행 (LLM 기반)
        
        Args:
            raw_data: 전체 raw_data 또는 서브트리
            simplified_structure: 전체 simplified_structure 또는 서브트리
            target_id: 특정 노드만 처리할 경우 해당 id
        
        Returns:
            처리된 JSON
        """
        # target_id가 지정되면 해당 서브트리만 처리
        if target_id and self.use_partial_loading:
            raw_data = extract_subtree(raw_data, target_id) or raw_data
            simplified_structure = extract_subtree(simplified_structure, target_id) or simplified_structure
        
        # raw_data를 복사하여 작업
        result = deepcopy(raw_data)
        
        # 검증 결과 초기화
        self.validation_results = []
        
        # 트리를 순회하며 처리
        self._process_node(result, simplified_structure, None, [], True)
        
        # Role 검증 리포트 생성 (활성화된 경우)
        if self.enable_role_validation and self.validation_results:
            report = self.role_validator.generate_validation_report(self.validation_results)
            result['_validation_report'] = report
            self._print_validation_summary(report)
        
        return result
    
    def _print_validation_summary(self, report: Dict):
        """검증 결과 요약 출력"""
        summary = report.get('summary', {})
        print("\n" + "=" * 60)
        print("📊 Role 검증 결과 요약")
        print("=" * 60)
        print(f"   총 노드 수: {summary.get('total_nodes', 0)}")
        print(f"   ✅ 유효한 노드: {summary.get('valid_nodes', 0)}")
        print(f"   ❌ 문제 있는 노드: {summary.get('invalid_nodes', 0)}")
        print(f"   📈 검증 통과율: {summary.get('validation_rate', 0):.1%}")
        print(f"   🎯 평균 신뢰도: {summary.get('average_confidence', 0):.1%}")
        
        issues_by_severity = report.get('issues_by_severity', {})
        if any(issues_by_severity.values()):
            print("\n   이슈 심각도:")
            if issues_by_severity.get('error', 0) > 0:
                print(f"      🔴 Error: {issues_by_severity['error']}개")
            if issues_by_severity.get('warning', 0) > 0:
                print(f"      🟡 Warning: {issues_by_severity['warning']}개")
            if issues_by_severity.get('info', 0) > 0:
                print(f"      🔵 Info: {issues_by_severity['info']}개")
        print("=" * 60)
    
    def _process_node(self, raw_node: Dict, simplified_node: Dict,
                     parent: Optional[Dict], siblings: List[Dict],
                     is_root: bool = False):
        """
        노드를 재귀적으로 처리 (매우 구체적인 과정)
        
        이 메서드는 각 노드에 대해 다음 단계를 순차적으로 수행합니다:
        
        0단계: Role 검증 (활성화된 경우)
        1단계: 컨텍스트 수집
        2단계: LLM Rule Analyzer 호출 → resizing 결정
        3단계: LLM Layout Analyzer 호출 → direction, gap, padding 결정
        4단계: LLM Alignment Enricher 호출 → alignment 결정
        5단계: 자식 노드 재귀 처리
        """
        
        node_id = raw_node.get('id', 'unknown')
        node_role = raw_node.get('role', 'N/A')
        print(f"🔄 처리 중: {node_id} (Role: {node_role})")
        
        # === 0단계: Role 검증 (활성화된 경우) ===
        structure_changed = False
        if self.enable_role_validation:
            print(f"   🔍 [Step 0] Role 검증 중...")
            try:
                raw_children = raw_node.get('children', [])
                children_count_before = len(raw_children)
                
                validation_result, was_modified = self.role_validator.validate_and_fix(
                    raw_node, parent, siblings, raw_children
                )
                self.validation_results.append(validation_result)
                
                # 구조 변경 감지 (children 수가 변했으면 구조 변경됨)
                children_count_after = len(raw_node.get('children', []))
                structure_changed = children_count_before != children_count_after
                
                if validation_result.get('is_valid'):
                    print(f"      ✅ Role 유효 (신뢰도: {validation_result.get('confidence', 0):.0%})")
                else:
                    issues_count = len(validation_result.get('issues', []))
                    print(f"      ⚠️ Role 문제 발견: {issues_count}개 이슈")
                    for issue in validation_result.get('issues', [])[:3]:  # 상위 3개만 출력
                        severity_icon = {'error': '🔴', 'warning': '🟡', 'info': '🔵'}.get(issue.get('severity', 'info'), '🔵')
                        print(f"         {severity_icon} {issue.get('description', '')}")
                    
                    if was_modified:
                        if structure_changed:
                            print(f"      🔧 구조 변경됨: {children_count_before}개 → {children_count_after}개 children")
                            # 새로 생성된 Group 정보 출력
                            for child in raw_node.get('children', []):
                                if child.get('_auto_generated'):
                                    print(f"         📦 새 Group 생성: {child.get('id')} ({len(child.get('children', []))}개 요소 포함)")
                        else:
                            print(f"      🔧 Role이 자동 수정되었습니다: {raw_node.get('role', '')}")
            except Exception as e:
                import traceback
                print(f"      ⚠️ Role 검증 오류: {e}")
                traceback.print_exc()
        
        # === 1단계: 컨텍스트 수집 ===
        # 주변 노드 정보를 수집하여 LLM에 전달할 컨텍스트 준비
        context_nodes = []
        if hasattr(self, '_collect_context'):
            context_nodes = self._collect_context(raw_node, 10)  # 최대 10개 노드
        
        print(f"   📦 컨텍스트 수집: {len(context_nodes)}개 노드")
        
        # === 2단계: LLM Rule Analyzer → Resizing 결정 ===
        print(f"   📐 [Step 1] Resizing 규칙 결정 중...")
        try:
            self.rule_analyzer.determine_resizing(
                raw_node, parent, siblings, context_nodes, is_root
            )
            print(f"      ✅ Resizing: {raw_node.get('resizing', 'N/A')}")
        except Exception as e:
            print(f"      ⚠️ Resizing 결정 오류: {e}")
            raw_node['resizing'] = 'fill * fill'  # 기본값
        
        # === 3단계: LLM Layout Analyzer → 레이아웃 속성 결정 ===
        print(f"   📏 [Step 2] 레이아웃 속성 결정 중...")
        try:
            self.layout_analyzer.analyze_and_enrich(raw_node, parent)
            print(f"      ✅ Direction: {raw_node.get('direction', 'N/A')}")
            print(f"      ✅ Gap: {raw_node.get('gap', 'N/A')}")
        except Exception as e:
            print(f"      ⚠️ 레이아웃 결정 오류: {e}")
            # 기본값 적용
            if 'direction' not in raw_node:
                raw_node['direction'] = 'vertical'
        
        # === 4단계: LLM Alignment Enricher → 정렬 속성 결정 ===
        print(f"   🎯 [Step 3] 정렬 속성 결정 중...")
        try:
            self.alignment_enricher.enrich_alignments(raw_node, parent)
            print(f"      ✅ Alignment: {raw_node.get('alignment', 'N/A')}")
        except Exception as e:
            print(f"      ⚠️ 정렬 결정 오류: {e}")
            # 기본값 적용
            if 'alignment' not in raw_node:
                raw_node['alignment'] = 'center'
        
        # === 5단계: 자식 노드 재귀 처리 ===
        raw_children = raw_node.get('children', [])
        simplified_children = simplified_node.get('children', [])
        
        if raw_children:
            print(f"   👶 [Step 4] 자식 노드 처리 시작 ({len(raw_children)}개)...")
            
            # id 기반 매칭 (simplified에서)
            simplified_by_id = {child.get('id'): child for child in simplified_children} if simplified_children else {}
            
            for i, raw_child in enumerate(raw_children):
                child_id = raw_child.get('id')
                
                # 자동 생성된 Group인 경우
                if raw_child.get('_auto_generated'):
                    print(f"      🆕 자동 생성된 Group 처리: {child_id}")
                    # 자동 생성된 Group은 자체적으로 simplified_node를 생성
                    auto_simplified = {
                        'id': child_id,
                        'type': raw_child.get('type', 'Group'),
                        'role': raw_child.get('role', ''),
                        'children': raw_child.get('children', [])  # 자식은 이미 raw_child에 있음
                    }
                    simplified_child = auto_simplified
                else:
                    simplified_child = simplified_by_id.get(child_id)
                
                if not simplified_child:
                    print(f"      ⚠️ 매칭되지 않은 자식: {child_id}")
                    # 매칭되지 않아도 raw_child 자체를 simplified로 사용하여 계속 처리
                    simplified_child = raw_child
                
                # 형제 노드들 (오른쪽 형제만)
                child_siblings = raw_children[i+1:] if i < len(raw_children) - 1 else []
                
                print(f"      🔽 자식 {i+1}/{len(raw_children)}: {child_id}")
                
                # 재귀 호출
                self._process_node(
                    raw_child,
                    simplified_child,
                    raw_node,  # 현재 노드가 부모가 됨
                    child_siblings,
                    False  # 더 이상 루트가 아님
                )
            
            print(f"   ✅ 자식 노드 처리 완료")
        
        print(f"✅ 완료: {node_id}\n")
    
    def process_node_by_id(self, raw_data_path: str, simplified_path: str,
                          target_id: str) -> Dict:
        """특정 id의 노드만 처리"""
        raw_data = load_json_partial(raw_data_path, target_id)
        simplified_structure = load_json_partial(simplified_path, target_id)
        return self.process(raw_data, simplified_structure, target_id)
    
    def validate_structure(self, simplified_structure: Dict) -> Dict:
        """
        Role 검증만 수행 (다른 속성 추가 없이)
        
        Args:
            simplified_structure: simplified_structure JSON
        
        Returns:
            검증 리포트가 포함된 결과
        """
        result = deepcopy(simplified_structure)
        self.validation_results = []
        
        # 검증만 수행하는 재귀 함수
        self._validate_node_recursive(result, None, [])
        
        # 검증 리포트 생성
        report = self.role_validator.generate_validation_report(self.validation_results)
        result['_validation_report'] = report
        self._print_validation_summary(report)
        
        return result
    
    def _validate_node_recursive(self, node: Dict, parent: Optional[Dict], 
                                  siblings: List[Dict]):
        """검증만 수행하는 재귀 함수"""
        node_id = node.get('id', 'unknown')
        node_role = node.get('role', 'N/A')
        
        print(f"🔍 검증 중: {node_id} (Role: {node_role})")
        
        children = node.get('children', [])
        
        # Role 검증
        try:
            validation_result, was_modified = self.role_validator.validate_and_fix(
                node, parent, siblings, children
            )
            self.validation_results.append(validation_result)
            
            if validation_result.get('is_valid'):
                print(f"   ✅ 유효 (신뢰도: {validation_result.get('confidence', 0):.0%})")
            else:
                issues_count = len(validation_result.get('issues', []))
                print(f"   ⚠️ 문제 발견: {issues_count}개 이슈")
                for issue in validation_result.get('issues', [])[:3]:
                    severity_icon = {'error': '🔴', 'warning': '🟡', 'info': '🔵'}.get(
                        issue.get('severity', 'info'), '🔵'
                    )
                    print(f"      {severity_icon} {issue.get('description', '')}")
        except Exception as e:
            print(f"   ⚠️ 검증 오류: {e}")
        
        # 자식 노드 재귀 처리
        for i, child in enumerate(children):
            child_siblings = children[i+1:] if i < len(children) - 1 else []
            self._validate_node_recursive(child, node, child_siblings)


class ParallelLLMCoordinatorAgent:
    """
    병렬 처리 버전의 LLM Coordinator (멀티모달 지원)
    - depth별로 노드를 그룹화하여 같은 depth는 동시 처리
    - asyncio를 사용한 비동기 병렬 호출
    - 참조 이미지를 통한 시각적 분석 지원
    """
    
    def __init__(self, llm_client, prompt_loader: Optional[PromptLoader] = None,
                 enable_role_validation: bool = True,
                 max_concurrent: int = 10,
                 reference_image_path: Optional[str] = None):
        """
        Args:
            llm_client: 동기 LLM 클라이언트 (OpenAI)
            prompt_loader: 프롬프트 로더
            enable_role_validation: Role 검증 활성화 여부
            max_concurrent: 최대 동시 요청 수
            reference_image_path: 참조 이미지 경로 (멀티모달 분석용)
        """
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader or PromptLoader()
        self.enable_role_validation = enable_role_validation
        self.max_concurrent = max_concurrent
        self.reference_image_path = reference_image_path
        
        # 에이전트들 (멀티모달 지원)
        self.role_validator = LLMRoleValidatorAgent(
            llm_client, self.prompt_loader, reference_image_path
        )
        self.rule_analyzer = LLMRuleAnalyzerAgent(llm_client, self.prompt_loader)
        self.layout_analyzer = LLMLayoutAnalyzerAgent(llm_client, self.prompt_loader)
        self.alignment_enricher = LLMAlignmentEnricherAgent(llm_client, self.prompt_loader)
        
        self.validation_results = []
        self._semaphore = None  # asyncio.Semaphore
    
    def process(self, raw_data: Dict, simplified_structure: Dict,
                target_id: Optional[str] = None) -> Dict:
        """
        병렬 처리로 전체 프로세스 실행
        """
        # raw_data를 복사하여 작업
        result = deepcopy(raw_data)
        self.validation_results = []
        
        # asyncio 이벤트 루프 실행
        try:
            # 이미 실행 중인 루프가 있는지 확인
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Jupyter 등에서 실행 중인 경우
                import nest_asyncio
                nest_asyncio.apply()
        except RuntimeError:
            pass
        
        asyncio.run(self._process_parallel(result, simplified_structure))
        
        # Role 검증 리포트 생성
        if self.enable_role_validation and self.validation_results:
            report = self.role_validator.generate_validation_report(self.validation_results)
            result['_validation_report'] = report
            self._print_validation_summary(report)
        
        return result
    
    def validate_only(self, raw_data: Dict, simplified_structure: Dict) -> Dict:
        """
        Role 검증만 병렬로 수행 (resizing, layout, alignment 스킵)
        """
        result = deepcopy(raw_data)
        self.validation_results = []
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
        except RuntimeError:
            pass
        
        asyncio.run(self._validate_parallel(result, simplified_structure))
        
        # Role 검증 리포트 생성
        if self.validation_results:
            report = self.role_validator.generate_validation_report(self.validation_results)
            result['_validation_report'] = report
            self._print_validation_summary(report)
        
        return result
    
    async def _validate_parallel(self, raw_data: Dict, simplified_structure: Dict):
        """
        Role 검증만 병렬 처리
        """
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        
        depth_groups = self._group_nodes_by_depth(raw_data, simplified_structure)
        
        total_nodes = sum(len(nodes) for nodes in depth_groups.values())
        print(f"\n📊 총 {total_nodes}개 노드를 {len(depth_groups)}개 depth로 검증")
        
        for depth in sorted(depth_groups.keys()):
            nodes_info = depth_groups[depth]
            print(f"\n🔍 [Depth {depth}] {len(nodes_info)}개 노드 검증 중...")
            
            tasks = []
            for node_info in nodes_info:
                task = self._validate_single_node_async(node_info)
                tasks.append(task)
            
            await asyncio.gather(*tasks)
            print(f"   ✅ [Depth {depth}] 완료")
    
    async def _validate_single_node_async(self, node_info: Dict):
        """
        단일 노드 Role 검증만 비동기로 처리 (resizing/layout/alignment 없음!)
        """
        async with self._semaphore:
            raw_node = node_info['raw_node']
            parent = node_info['parent']
            siblings = node_info['siblings']
            
            node_id = raw_node.get('id', 'unknown')
            
            loop = asyncio.get_event_loop()
            
            try:
                # Role Validation만 수행!
                children = raw_node.get('children', [])
                validation_result = await loop.run_in_executor(
                    None,
                    lambda: self._run_validation(raw_node, parent, siblings, children)
                )
                if validation_result:
                    self.validation_results.append(validation_result)
                
                print(f"      ✓ {node_id}")
                
            except Exception as e:
                print(f"      ✗ {node_id}: {e}")
    
    async def _process_parallel(self, raw_data: Dict, simplified_structure: Dict):
        """
        depth별 병렬 처리 메인 로직
        """
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        
        # 1. 노드를 depth별로 그룹화
        depth_groups = self._group_nodes_by_depth(raw_data, simplified_structure)
        
        total_nodes = sum(len(nodes) for nodes in depth_groups.values())
        print(f"\n📊 총 {total_nodes}개 노드를 {len(depth_groups)}개 depth로 병렬 처리")
        
        # 2. depth 순서대로 처리 (각 depth 내에서는 병렬)
        for depth in sorted(depth_groups.keys()):
            nodes_info = depth_groups[depth]
            print(f"\n🔄 [Depth {depth}] {len(nodes_info)}개 노드 동시 처리 중...")
            
            # 같은 depth의 노드들을 동시에 처리
            tasks = []
            for node_info in nodes_info:
                task = self._process_single_node_async(node_info)
                tasks.append(task)
            
            # 모든 태스크 동시 실행
            await asyncio.gather(*tasks)
            
            print(f"   ✅ [Depth {depth}] 완료")
    
    def _group_nodes_by_depth(self, raw_data: Dict, simplified_data: Dict) -> Dict[int, List[Dict]]:
        """
        트리의 모든 노드를 depth별로 그룹화
        
        Returns:
            {depth: [(raw_node, simplified_node, parent, siblings), ...]}
        """
        depth_groups = {}
        
        def traverse(raw_node: Dict, simplified_node: Dict, 
                    parent: Optional[Dict], siblings: List[Dict], depth: int):
            """DFS로 트리 순회하며 노드 정보 수집"""
            
            if depth not in depth_groups:
                depth_groups[depth] = []
            
            # 노드 정보 저장
            depth_groups[depth].append({
                'raw_node': raw_node,
                'simplified_node': simplified_node,
                'parent': parent,
                'siblings': siblings,
                'depth': depth
            })
            
            # 자식 노드 처리
            raw_children = raw_node.get('children', [])
            simplified_children = simplified_node.get('children', []) if simplified_node else []
            simplified_by_id = {c.get('id'): c for c in simplified_children}
            
            for i, raw_child in enumerate(raw_children):
                child_id = raw_child.get('id')
                simplified_child = simplified_by_id.get(child_id, raw_child)
                child_siblings = raw_children[i+1:] if i < len(raw_children) - 1 else []
                
                traverse(raw_child, simplified_child, raw_node, child_siblings, depth + 1)
        
        traverse(raw_data, simplified_data, None, [], 0)
        return depth_groups
    
    async def _process_single_node_async(self, node_info: Dict):
        """
        단일 노드를 비동기로 처리
        """
        async with self._semaphore:  # 동시 요청 수 제한
            raw_node = node_info['raw_node']
            parent = node_info['parent']
            siblings = node_info['siblings']
            
            node_id = raw_node.get('id', 'unknown')
            
            # ThreadPoolExecutor를 사용해 동기 함수를 비동기로 실행
            loop = asyncio.get_event_loop()
            
            try:
                # Step 0: Role Validation (병렬)
                if self.enable_role_validation:
                    children = raw_node.get('children', [])
                    validation_result = await loop.run_in_executor(
                        None,
                        lambda: self._run_validation(raw_node, parent, siblings, children)
                    )
                    if validation_result:
                        self.validation_results.append(validation_result)
                
                # Step 1: Resizing (병렬)
                await loop.run_in_executor(
                    None,
                    lambda: self._run_resizing(raw_node, parent, siblings)
                )
                
                # Step 2: Layout (병렬)
                await loop.run_in_executor(
                    None,
                    lambda: self._run_layout(raw_node, parent)
                )
                
                # Step 3: Alignment (병렬)
                await loop.run_in_executor(
                    None,
                    lambda: self._run_alignment(raw_node, parent)
                )
                
                print(f"      ✓ {node_id}")
                
            except Exception as e:
                print(f"      ✗ {node_id}: {e}")
    
    def _run_validation(self, node: Dict, parent: Optional[Dict], 
                       siblings: List[Dict], children: List[Dict]) -> Optional[Dict]:
        """Role Validation 실행"""
        try:
            result, _ = self.role_validator.validate_and_fix(node, parent, siblings, children)
            return result
        except Exception as e:
            print(f"      ⚠️ Validation 오류: {e}")
            return None
    
    def _run_resizing(self, node: Dict, parent: Optional[Dict], siblings: List[Dict]):
        """Resizing 실행"""
        try:
            self.rule_analyzer.determine_resizing(node, parent, siblings, [], False)
        except Exception as e:
            node['resizing'] = 'fill * fill'
    
    def _run_layout(self, node: Dict, parent: Optional[Dict]):
        """Layout 실행"""
        try:
            self.layout_analyzer.analyze_and_enrich(node, parent)
        except Exception as e:
            if 'direction' not in node:
                node['direction'] = 'vertical'
    
    def _run_alignment(self, node: Dict, parent: Optional[Dict]):
        """Alignment 실행"""
        try:
            self.alignment_enricher.enrich_alignments(node, parent)
        except Exception as e:
            if 'alignment' not in node:
                node['alignment'] = 'center'
    
    def _print_validation_summary(self, report: Dict):
        """검증 결과 요약 출력"""
        summary = report.get('summary', {})
        print("\n" + "=" * 60)
        print("📊 Role 검증 결과 요약")
        print("=" * 60)
        print(f"   총 노드 수: {summary.get('total_nodes', 0)}")
        print(f"   ✅ 유효한 노드: {summary.get('valid_nodes', 0)}")
        print(f"   ❌ 문제 있는 노드: {summary.get('invalid_nodes', 0)}")
        print(f"   📈 검증 통과율: {summary.get('validation_rate', 0):.1%}")
        print("=" * 60)


def main():
    """메인 실행 함수"""
    import sys
    import os
    from json_utils import save_simplified_structure
    
    # 파일 경로
    simplified_path = 'data/simplified_structure.json'
    raw_data_path = 'data/raw_data.json'
    output_path = 'data/llm_enriched_output.json'
    
    # raw_data.json 존재 확인
    if not os.path.exists(raw_data_path):
        print(f"❌ raw_data.json을 찾을 수 없습니다: {raw_data_path}")
        return
    
    # simplified_structure.json 없으면 자동 생성
    if not os.path.exists(simplified_path):
        print(f"📝 simplified_structure.json이 없습니다. 자동 생성 중...")
        try:
            save_simplified_structure(raw_data_path, simplified_path)
        except Exception as e:
            print(f"❌ simplified_structure 생성 실패: {e}")
            return
    
    # LLM 클라이언트 설정
    llm_client = None
    try:
        from openai import OpenAI
        api_key = os.getenv('OPENAI_API_KEY')
        if api_key:
            llm_client = OpenAI(api_key=api_key)
            print("✅ OpenAI 클라이언트 초기화 완료\n")
        else:
            print("❌ OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
            print("   export OPENAI_API_KEY='your-api-key' 로 설정하세요.")
            return
    except ImportError:
        print("❌ openai 패키지가 설치되지 않았습니다.")
        print("   pip install openai 로 설치하세요.")
        return
    
    # 처리 모드 선택
    use_partial = '--partial' in sys.argv
    enable_validation = '--no-validation' not in sys.argv
    validation_only = '--validate-only' in sys.argv
    use_parallel = '--parallel' in sys.argv
    target_id = None
    max_concurrent = 10  # 기본 동시 요청 수
    reference_image_path = None  # 멀티모달 참조 이미지
    
    if '--node' in sys.argv:
        idx = sys.argv.index('--node')
        if idx + 1 < len(sys.argv):
            target_id = sys.argv[idx + 1]
            use_partial = True
    
    if '--concurrent' in sys.argv:
        idx = sys.argv.index('--concurrent')
        if idx + 1 < len(sys.argv):
            try:
                max_concurrent = int(sys.argv[idx + 1])
            except ValueError:
                pass
    
    # 참조 이미지 경로
    if '--image' in sys.argv:
        idx = sys.argv.index('--image')
        if idx + 1 < len(sys.argv):
            reference_image_path = sys.argv[idx + 1]
            if not os.path.exists(reference_image_path):
                print(f"⚠️ 참조 이미지를 찾을 수 없습니다: {reference_image_path}")
                reference_image_path = None
    
    print("=" * 60)
    print("LLM 전용 멀티 에이전트 시스템 (멀티모달 지원)")
    print("=" * 60)
    print()
    print(f"📋 설정:")
    print(f"   - Role 검증: {'✅ 활성화' if enable_validation else '❌ 비활성화'}")
    print(f"   - Role 검증+수정만: {'✅ 예 (resizing/layout/alignment 스킵)' if validation_only else '❌ 아니오 (전체 처리)'}")
    print(f"   - 병렬 처리: {'✅ 활성화 (동시 ' + str(max_concurrent) + '개)' if use_parallel else '❌ 비활성화 (순차)'}")
    print(f"   - 참조 이미지: {'📷 ' + reference_image_path if reference_image_path else '❌ 없음 (JSON만 분석)'}")
    print()
    
    if use_partial and target_id:
        print(f"🔍 특정 노드만 처리: {target_id}\n")
        coordinator = LLMCoordinatorAgent(
            llm_client, 
            use_partial_loading=True,
            enable_role_validation=enable_validation,
            reference_image_path=reference_image_path
        )
        result = coordinator.process_node_by_id(
            raw_data_path, simplified_path, target_id
        )
    else:
        print("📦 전체 파일 처리 중...\n")
        with open(simplified_path, 'r', encoding='utf-8') as f:
            simplified_structure = json.load(f)
        
        with open(raw_data_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        
        import time
        start_time = time.time()
        
        if use_parallel:
            # 병렬 처리 모드
            print("⚡ 병렬 처리 모드로 실행합니다...\n")
            coordinator = ParallelLLMCoordinatorAgent(
                llm_client,
                enable_role_validation=enable_validation,
                max_concurrent=max_concurrent,
                reference_image_path=reference_image_path
            )
            
            if validation_only:
                # Role 검증+수정만 (병렬)
                print("🔍 Role 검증+수정만 수행합니다 (병렬)...\n")
                result = coordinator.validate_only(raw_data, simplified_structure)
            else:
                result = coordinator.process(raw_data, simplified_structure, target_id)
        else:
            # 순차 처리 모드
            coordinator = LLMCoordinatorAgent(
                llm_client, 
                use_partial_loading=use_partial,
                enable_role_validation=enable_validation,
                reference_image_path=reference_image_path
            )
            
            if validation_only:
                # Role 검증+수정만
                print("🔍 Role 검증+수정만 수행합니다...\n")
                result = coordinator.validate_structure(simplified_structure)
            else:
                result = coordinator.process(raw_data, simplified_structure, target_id)
        
        elapsed_time = time.time() - start_time
        print(f"\n⏱️ 처리 시간: {elapsed_time:.1f}초")
    
    # 결과 저장
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print("\n" + "=" * 60)
    print(f"✅ 처리 완료! 결과가 {output_path}에 저장되었습니다.")
    print("=" * 60)


if __name__ == '__main__':
    main()
