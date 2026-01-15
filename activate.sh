#!/bin/bash

# 가상환경 디렉토리 이름
VENV_DIR="venv"

# 가상환경이 없으면 생성
if [ ! -d "$VENV_DIR" ]; then
    echo "가상환경이 없습니다. 생성 중..."
    python3 -m venv $VENV_DIR
    if [ $? -eq 0 ]; then
        echo "✅ 가상환경 생성 완료!"
    else
        echo "❌ 가상환경 생성 실패"
        exit 1
    fi
fi

# 가상환경 활성화
source $VENV_DIR/bin/activate

echo "✅ 가상환경이 활성화되었습니다!"
echo "비활성화하려면 'deactivate' 명령을 사용하세요."
