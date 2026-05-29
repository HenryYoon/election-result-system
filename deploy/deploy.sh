#!/bin/bash
# deploy/deploy.sh
# AWS EC2 c5.xlarge 인스턴스 생성 + 코드 배포 자동화
#
# 사전 요구사항:
#   - aws cli 설치 및 aws configure 완료
#   - .env 파일이 프로젝트 루트에 존재
#   - ssh 키페어가 ~/.ssh/${KEY_NAME}.pem 에 존재
#
# 사용법:
#   chmod +x deploy/deploy.sh
#   ./deploy/deploy.sh

set -e

# =============================================
# 설정 (필요시 수정)
# =============================================
KEY_NAME="election-key"            # AWS 키페어 이름
REGION="ap-northeast-2"           # 서울 리전 (us-east-1 등으로 변경 가능)
INSTANCE_TYPE="c5.xlarge"
AMI_ID="ami-0c9c942bd7bf113a2"    # Amazon Linux 2023 (서울 리전)
SECURITY_GROUP_NAME="election-sg"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="/home/ec2-user/election-result-system"

echo "===== 사전투표 수집 시스템 AWS 배포 시작 ====="
echo "리전: $REGION | 인스턴스: $INSTANCE_TYPE"

# =============================================
# 1. 보안 그룹 생성 (이미 있으면 기존 것 사용)
# =============================================
echo "[1/6] 보안 그룹 설정..."

SG_ID=$(aws ec2 describe-security-groups \
  --region "$REGION" \
  --filters "Name=group-name,Values=$SECURITY_GROUP_NAME" \
  --query "SecurityGroups[0].GroupId" \
  --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group \
    --region "$REGION" \
    --group-name "$SECURITY_GROUP_NAME" \
    --description "Election result system security group" \
    --query "GroupId" \
    --output text)
  echo "보안 그룹 생성: $SG_ID"

  # SSH
  aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp --port 22 --cidr 0.0.0.0/0

  # Streamlit 검수 앱
  aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp --port 8501 --cidr 0.0.0.0/0

  # Streamlit 대시보드
  aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp --port 8502 --cidr 0.0.0.0/0
else
  echo "기존 보안 그룹 사용: $SG_ID"
fi

# =============================================
# 2. 키페어 생성 (없으면)
# =============================================
echo "[2/6] 키페어 확인..."

if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" &>/dev/null; then
  aws ec2 create-key-pair \
    --region "$REGION" \
    --key-name "$KEY_NAME" \
    --query "KeyMaterial" \
    --output text > ~/.ssh/${KEY_NAME}.pem
  chmod 400 ~/.ssh/${KEY_NAME}.pem
  echo "키페어 생성: ~/.ssh/${KEY_NAME}.pem"
else
  echo "기존 키페어 사용: $KEY_NAME"
fi

# =============================================
# 3. EC2 인스턴스 생성
# =============================================
echo "[3/6] EC2 인스턴스 생성..."

INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=election-result-system}]" \
  --query "Instances[0].InstanceId" \
  --output text)

echo "인스턴스 생성: $INSTANCE_ID"
echo "인스턴스 시작 대기 중..."

aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].PublicIpAddress" \
  --output text)

echo "퍼블릭 IP: $PUBLIC_IP"

# SSH 접속 가능할 때까지 대기
echo "SSH 준비 대기 중 (최대 2분)..."
for i in $(seq 1 24); do
  if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    -i ~/.ssh/${KEY_NAME}.pem ec2-user@"$PUBLIC_IP" "echo ok" &>/dev/null; then
    echo "SSH 연결 성공"
    break
  fi
  sleep 5
done

# =============================================
# 4. 서버 환경 설치 (Docker, Docker Compose)
# =============================================
echo "[4/6] 서버 환경 설치..."

ssh -o StrictHostKeyChecking=no \
  -i ~/.ssh/${KEY_NAME}.pem \
  ec2-user@"$PUBLIC_IP" << 'REMOTE_SETUP'
set -e

# Docker 설치
sudo yum update -y
sudo yum install -y docker git
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# Docker Compose 설치
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

echo "Docker 버전: $(docker --version)"
echo "Docker Compose 버전: $(docker-compose --version)"
REMOTE_SETUP

# =============================================
# 5. 코드 및 설정 파일 전송
# =============================================
echo "[5/6] 코드 전송..."

# .gitignore에 포함된 파일 제외하고 rsync
ssh -o StrictHostKeyChecking=no \
  -i ~/.ssh/${KEY_NAME}.pem \
  ec2-user@"$PUBLIC_IP" "mkdir -p $REMOTE_DIR"

rsync -avz \
  --exclude='.git' \
  --exclude='data/' \
  --exclude='images/' \
  --exclude='ocr_results/' \
  --exclude='approved/' \
  --exclude='confidential/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.venv/' \
  --exclude='venv/' \
  -e "ssh -o StrictHostKeyChecking=no -i ~/.ssh/${KEY_NAME}.pem" \
  "$PROJECT_DIR/" \
  "ec2-user@${PUBLIC_IP}:${REMOTE_DIR}/"

# .env 파일 전송 (별도로 — rsync 제외 목록에 없지만 명시적으로)
scp -o StrictHostKeyChecking=no \
  -i ~/.ssh/${KEY_NAME}.pem \
  "$PROJECT_DIR/.env" \
  "ec2-user@${PUBLIC_IP}:${REMOTE_DIR}/.env"

echo "코드 전송 완료"

# =============================================
# 6. Docker Compose 실행
# =============================================
echo "[6/6] Docker Compose 실행..."

ssh -o StrictHostKeyChecking=no \
  -i ~/.ssh/${KEY_NAME}.pem \
  ec2-user@"$PUBLIC_IP" << REMOTE_RUN
set -e
cd $REMOTE_DIR

# docker 그룹 반영을 위해 newgrp 없이 sudo 사용
sudo docker-compose pull 2>/dev/null || true
sudo docker-compose up -d --build

echo "실행 중인 컨테이너:"
sudo docker-compose ps
REMOTE_RUN

# =============================================
# 완료
# =============================================
echo ""
echo "===== 배포 완료 ====="
echo "인스턴스 ID : $INSTANCE_ID"
echo "퍼블릭 IP   : $PUBLIC_IP"
echo ""
echo "접속 정보:"
echo "  SSH        : ssh -i ~/.ssh/${KEY_NAME}.pem ec2-user@${PUBLIC_IP}"
echo "  검수 앱    : http://${PUBLIC_IP}:8501"
echo "  대시보드   : http://${PUBLIC_IP}:8502"
echo ""
echo "Streamlit 앱은 서버에서 직접 실행해야 합니다:"
echo "  ssh -i ~/.ssh/${KEY_NAME}.pem ec2-user@${PUBLIC_IP}"
echo "  cd $REMOTE_DIR"
echo "  nohup streamlit run review/app.py --server.port 8501 &"
echo "  nohup streamlit run review/dashboard.py --server.port 8502 &"
echo ""
echo "로그 확인:"
echo "  sudo docker-compose -f $REMOTE_DIR/docker-compose.yml logs -f"
