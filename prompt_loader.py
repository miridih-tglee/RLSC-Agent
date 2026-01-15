"""
프롬프트 로더: YAML 파일에서 프롬프트 로드 및 템플릿 생성
"""

import yaml
import json
from typing import Dict, Any
from pathlib import Path


class PromptLoader:
    """YAML 파일에서 프롬프트를 로드하고 템플릿을 생성"""
    
    def __init__(self, prompts_dir: str = "prompts"):
        """
        Args:
            prompts_dir: 프롬프트 YAML 파일이 있는 디렉토리
        """
        self.prompts_dir = Path(prompts_dir)
        self._prompts = {}
        self._load_all_prompts()
    
    def _load_all_prompts(self):
        """모든 프롬프트 파일 로드"""
        prompt_files = {
            'resizing': 'resizing.yaml',
            'layout': 'layout.yaml',
            'alignment': 'alignment.yaml',
            'role_validation': 'role_validation.yaml'
        }
        
        for key, filename in prompt_files.items():
            filepath = self.prompts_dir / filename
            if filepath.exists():
                with open(filepath, 'r', encoding='utf-8') as f:
                    self._prompts[key] = yaml.safe_load(f)
            else:
                print(f"⚠️ 프롬프트 파일을 찾을 수 없습니다: {filepath}")
    
    def get_prompt(self, prompt_type: str, context: Dict[str, Any]) -> str:
        """
        프롬프트 템플릿 생성
        
        Args:
            prompt_type: 'resizing', 'layout', 'alignment'
            context: 프롬프트에 삽입할 컨텍스트 정보
        
        Returns:
            완성된 프롬프트 문자열
        """
        if prompt_type not in self._prompts:
            raise ValueError(f"알 수 없는 프롬프트 타입: {prompt_type}")
        
        prompt_config = self._prompts[prompt_type]
        
        # 프롬프트 구성
        parts = []
        
        # 시스템 역할
        if 'system_role' in prompt_config:
            parts.append(prompt_config['system_role'])
            parts.append("")
        
        # 작업 설명
        if 'task_description' in prompt_config:
            parts.append(prompt_config['task_description'])
            parts.append("")
        
        # 노드 정보
        if 'node_info' in context:
            parts.append("## 📋 현재 노드 정보")
            parts.append(json.dumps(context['node_info'], indent=2, ensure_ascii=False))
            parts.append("")
        
        # 부모 정보
        if 'parent_info' in context:
            parts.append("## 👆 부모 노드 정보")
            if context['parent_info']:
                parts.append(json.dumps(context['parent_info'], indent=2, ensure_ascii=False))
            else:
                parts.append("None (최상위 노드)")
            parts.append("")
        
        # 형제 정보
        if 'siblings_info' in context:
            parts.append("## 👉 형제 노드 정보")
            if context['siblings_info']:
                parts.append(json.dumps(context['siblings_info'], indent=2, ensure_ascii=False))
            else:
                parts.append("None (형제 없음)")
            parts.append("")
        
        # 컨텍스트 정보
        if 'context_summary' in context:
            parts.append("## 🌐 주변 컨텍스트")
            parts.append(json.dumps(context['context_summary'], indent=2, ensure_ascii=False))
            parts.append("")
        
        # 가이드 추가
        if prompt_type == 'resizing' and 'resizing_guide' in prompt_config:
            parts.append("## 📐 Resizing 규칙 가이드")
            parts.append(prompt_config['resizing_guide'])
            parts.append("")
            
            # Role 패턴
            if 'role_patterns' in prompt_config:
                parts.append("### Role별 일반적인 패턴:")
                parts.append("")
                pattern_num = 1
                for role, pattern in prompt_config['role_patterns'].items():
                    parts.append(f"{pattern_num}. **{role}** ({pattern.get('description', '')})")
                    for p in pattern.get('patterns', []):
                        parts.append(f"   - {p.get('condition', '')} → `{p.get('resizing', '')}`")
                    parts.append("")
                    pattern_num += 1
        
        elif prompt_type == 'layout' and 'layout_guide' in prompt_config:
            parts.append("## 📐 레이아웃 속성 가이드")
            parts.append(prompt_config['layout_guide'])
            parts.append("")
        
        elif prompt_type == 'alignment' and 'alignment_guide' in prompt_config:
            parts.append("## 📐 Alignment 가이드")
            parts.append(prompt_config['alignment_guide'])
            parts.append("")
        
        elif prompt_type == 'role_validation':
            # Role 정의 추가
            if 'role_definitions' in prompt_config:
                parts.append("## 📚 Role 정의")
                parts.append("")
                
                role_defs = prompt_config['role_definitions']
                
                # Page Roles
                if 'page_roles' in role_defs:
                    parts.append("### Role.Page (페이지 레벨)")
                    for role in role_defs['page_roles']:
                        parts.append(f"- **{role['name']}**: {role['description']}")
                    parts.append("")
                
                # LayoutContainer Roles
                if 'layout_container_roles' in role_defs:
                    parts.append("### Role.LayoutContainer (레이아웃 컨테이너)")
                    for role in role_defs['layout_container_roles']:
                        parts.append(f"- **{role['name']}**: {role['description']}")
                    parts.append("")
                
                # Element Roles
                if 'element_roles' in role_defs:
                    parts.append("### Role.Element (개별 요소)")
                    for role in role_defs['element_roles']:
                        constraints = role.get('constraints', '')
                        parts.append(f"- **{role['name']}**: {role['description']}")
                        if constraints:
                            parts.append(f"  - 제약: {constraints}")
                    parts.append("")
            
            # Layout Type 정의
            if 'layout_type_definitions' in prompt_config:
                parts.append("### Layout Types")
                for lt in prompt_config['layout_type_definitions']:
                    parts.append(f"- **{lt['name']}**: {lt['description']} (조건: {lt['condition']})")
                parts.append("")
            
            # 검증 규칙
            if 'validation_rules' in prompt_config:
                parts.append("## ✅ 검증 규칙")
                parts.append(prompt_config['validation_rules'])
                parts.append("")
            
            # 자식 노드 정보 (role_validation 전용)
            if 'children_info' in context:
                parts.append("## 👶 자식 노드 정보")
                if context['children_info']:
                    parts.append(json.dumps(context['children_info'], indent=2, ensure_ascii=False))
                else:
                    parts.append("None (자식 없음)")
                parts.append("")
        
        # 분석 지시
        if 'analysis_instructions' in prompt_config:
            parts.append("## 🎯 분석 지시")
            parts.append(prompt_config['analysis_instructions'])
            parts.append("")
        
        # 출력 형식
        if 'output_format' in prompt_config:
            parts.append("## 📤 출력 형식")
            parts.append(prompt_config['output_format'])
            parts.append("")
        
        # 출력 요구사항
        if 'output_requirements' in prompt_config:
            parts.append("**중요**:")
            for req in prompt_config['output_requirements'].split('\n'):
                if req.strip():
                    parts.append(f"- {req.strip()}")
        
        return "\n".join(parts)
    
    def get_llm_config(self, prompt_type: str) -> Dict[str, Any]:
        """LLM 설정 가져오기"""
        if prompt_type not in self._prompts:
            return {}
        
        return self._prompts[prompt_type].get('llm_config', {
            'model': 'gpt-4',
            'temperature': 0.2,
            'max_tokens': 200
        })
