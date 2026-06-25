# Deploying health-checker to AWS (cheap EC2, via CDK)

This deploys the app to **one tiny EC2 instance** (t4g.micro) that runs the two
containers (nginx + FastAPI) behind an Elastic IP — defined entirely in CDK. There's
no load balancer and no NAT gateway, so cost is ~$0 on free tier (a few $/mo after).

It's all **Infrastructure as Code**: `cdk deploy` creates/updates the environment,
`cdk destroy` tears it down. Deploy to demo/learn, destroy to stop paying.

## What gets created (per environment)
DynamoDB table · a small VPC (public subnet, **no NAT**) · a security group (inbound
:80 only) · an IAM role (DynamoDB + ECR + SSM read; no keys) · the two Docker images
(built from `backend/`+`frontend/`, pushed to ECR) · a t4g.micro EC2 + Elastic IP that
runs the containers via a boot script.

## Access is gated
nginx requires **HTTP Basic Auth** (user `admin` + a generated password). The password
lives in SSM; the box reads it at boot. This stops random bots from abusing the paid
`/api/chat` endpoint. (Plain HTTP for now — HTTPS is a future follow-up.)

## Prerequisites (already done once)
- AWS CLI configured as profile **`personal`** (account 480566308626, us-east-1).
- CDK CLI installed; `infra/.venv` created (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
- Account bootstrapped once: `AWS_PROFILE=personal cdk bootstrap aws://480566308626/us-east-1`.
- Docker running locally (CDK builds the images during deploy).

## Deploy (from the `infra/` directory)

```bash
# 1) Create this env's secrets in SSM (reads API keys from ../backend/.env, prints the
#    app login password the first time). Run once per environment.
./setup-secrets.sh dev

# 2) Build images + create the box. (Creates an IAM role, hence --require-approval never.)
export JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION=1
AWS_PROFILE=personal cdk deploy HealthChecker-dev --require-approval never
#    -> note the "AppUrl" output. First boot takes ~2–4 min (installs Docker, pulls images).
```

## Verify
- Open the `AppUrl` in a browser → it asks for a password → log in `admin` / `<password>`.
- Ask a medical question → the answer streams and a new item appears in the
  `health-checker-conversations-dev` DynamoDB table.
- Quick API check (no browser): `curl -u admin:PASSWORD http://<ip>/api/conversations/none`
  should return `404` (proves nginx → backend → DynamoDB all work).

## Shell into the box (no SSH port — uses SSM Session Manager)
```bash
AWS_PROFILE=personal aws ssm start-session --target <instance-id>
# then: sudo docker ps ; sudo docker logs backend ; sudo docker logs frontend
```

## Update (deploy new code)
Rebuild + roll out by re-running the deploy. A code change → new image hash → new boot
script → CDK replaces the instance (~2–3 min), keeping the same Elastic IP:
```bash
AWS_PROFILE=personal cdk deploy HealthChecker-dev --require-approval never
```

## Tear down (stop all cost)
```bash
AWS_PROFILE=personal cdk destroy HealthChecker-dev
```
This removes the box, Elastic IP, VPC, role, and (for dev) the table. The SSM secrets
and the one-time bootstrap stack remain, so redeploy is just the Deploy steps again.

## Cost notes
t4g.micro (free-tier eligible), no NAT, no load balancer, SSM Parameter Store (free),
DynamoDB on-demand, Elastic IP (free while attached to a running instance). Destroying
between sessions keeps it to cents.
