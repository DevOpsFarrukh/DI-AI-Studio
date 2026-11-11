#!/usr/bin/env bash
# Build the runtime image for x86_64 and push it to Amazon ECR.
#
#   AWS_REGION=eu-central-1 deploy/aws/push-to-ecr.sh            # tag = git short SHA
#   AWS_REGION=eu-central-1 TAG=v0.3.0 deploy/aws/push-to-ecr.sh
#
# Needs: Docker, AWS CLI v2 with credentials allowed to use ECR
# (ecr:CreateRepository, ecr:GetAuthorizationToken, ecr:*Upload*, ecr:PutImage, ...).
# The repository is created on first use with scan-on-push and immutable tags.
set -euo pipefail

: "${AWS_REGION:?set AWS_REGION, e.g. AWS_REGION=eu-central-1}"
REPO="${ECR_REPO:-di-ai-studio-tts}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
cd "$(dirname "$0")/../.."

if [ -n "$(git status --porcelain)" ]; then
  echo "warning: uncommitted changes; the image will not match commit $TAG" >&2
fi

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:${TAG}"

aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$REPO" >/dev/null 2>&1 ||
  aws ecr create-repository --region "$AWS_REGION" --repository-name "$REPO" \
    --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE >/dev/null

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# Run the test stage first: a failing test stops the push.
docker build --platform linux/amd64 --target test -t di-tts:test .
docker build --platform linux/amd64 --target runtime -t "$IMAGE" .
docker push "$IMAGE"

echo
echo "Pushed: $IMAGE"
echo "Use it in deploy/aws/ecs-task-definition.json (\"image\") or on EC2 with: docker pull $IMAGE"
