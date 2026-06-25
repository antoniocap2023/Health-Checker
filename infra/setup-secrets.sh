#!/usr/bin/env bash
# Create/update the SSM Parameter Store secrets one environment needs, BEFORE deploy.
# These live outside CDK on purpose, so secret values never enter the code or the
# CloudFormation template — and they survive `cdk destroy`/redeploy.
#
#   ./setup-secrets.sh [env]        # env defaults to "dev"
#
# - The two API keys are read from ../backend/.env and are NEVER printed.
# - The Basic Auth password is generated ONCE and printed so you can log in.
#   Re-running leaves an existing password unchanged (so you don't get locked out).
set -euo pipefail

ENV="${1:-dev}"
REGION="us-east-1"
PROFILE="personal"
PREFIX="/health-checker/${ENV}"
ENV_FILE="$(cd "$(dirname "$0")/../backend" && pwd)/.env"

[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE not found" >&2; exit 1; }

read_env() { grep "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }
put() {  # name value  — write a SecureString param
  AWS_PROFILE="$PROFILE" aws ssm put-parameter --region "$REGION" \
    --type SecureString --overwrite --name "$1" --value "$2" >/dev/null
}

echo "Writing API-key secrets under ${PREFIX} (values not shown)…"
put "${PREFIX}/anthropic-api-key" "$(read_env ANTHROPIC_API_KEY)"
put "${PREFIX}/ncbi-api-key"      "$(read_env NCBI_API_KEY)"

if AWS_PROFILE="$PROFILE" aws ssm get-parameter --region "$REGION" \
     --name "${PREFIX}/basic-auth-password" >/dev/null 2>&1; then
  echo "Basic Auth password already exists — leaving it unchanged."
else
  PW="$(openssl rand -base64 18)"
  put "${PREFIX}/basic-auth-password" "$PW"
  echo "------------------------------------------------------------------"
  echo "  APP LOGIN   user: admin    password: ${PW}"
  echo "  (save it; later:  aws ssm get-parameter --name ${PREFIX}/basic-auth-password"
  echo "                     --with-decryption --query Parameter.Value --output text"
  echo "                     --profile ${PROFILE} --region ${REGION})"
  echo "------------------------------------------------------------------"
fi
echo "Done."
