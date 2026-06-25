"""The HealthChecker stack — the whole environment, described as code.

This one stack defines everything one environment needs, the cheap way (no load
balancer, no ECS): a tiny EC2 box that runs the two Docker containers (nginx +
FastAPI) exactly like docker-compose does locally.

Resources created (per environment):
  - DynamoDB table            (conversations, per-env name)
  - VPC + public subnet       (no NAT gateway → free)
  - Security group            (inbound :80 only; Basic Auth gates it)
  - IAM instance role         (DynamoDB access, ECR pull, SSM param read, Session Manager)
  - Docker image assets       (CDK builds backend/ and frontend/ images → ECR)
  - EC2 t4g.micro + Elastic IP (runs the containers via the boot script below)

The stack is shaped by `env_name` ("dev"/"prod"), which is the seam that lets the
SAME code produce two independent environments (Stage 4c).

Secrets (API keys + the Basic Auth password) are NOT in this file. They live in SSM
Parameter Store (created out-of-band), and the boot script fetches them at runtime.
The instance role is what grants boto3 its AWS credentials — no keys anywhere.
"""
import os

from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_ecr_assets as ecr_assets,
)
from constructs import Construct

# Paths to the app code, relative to this file (infra/health_checker_stack.py).
# CDK builds Docker images from these directories using their existing Dockerfiles.
_HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.normpath(os.path.join(_HERE, "..", "backend"))
FRONTEND_DIR = os.path.normpath(os.path.join(_HERE, "..", "frontend"))


class HealthCheckerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str,
                 instance_type: str = "t4g.micro", **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        account = self.account            # concrete because app.py pins env=Environment(...)
        region = self.region
        table_name = f"health-checker-conversations-{env_name}"
        ssm_prefix = f"/health-checker/{env_name}"   # where the secrets live in SSM
        basic_auth_user = "admin"         # username is not secret; password comes from SSM

        # ---- DynamoDB table ------------------------------------------------
        removal_policy = RemovalPolicy.DESTROY if env_name == "dev" else RemovalPolicy.RETAIN
        table = dynamodb.Table(
            self,
            "ConversationsTable",
            table_name=table_name,
            partition_key=dynamodb.Attribute(
                name="conversation_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal_policy,
        )

        # ---- Network: a minimal VPC, public subnet, NO NAT (NAT costs ~$32/mo) --
        # The box sits in a public subnet with a public IP so it can reach Anthropic
        # / NCBI / DynamoDB directly. One AZ is plenty for a single box.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )

        # ---- Security group: only inbound port 80 (Basic Auth gates access) ----
        # No SSH port — we use SSM Session Manager for shell access (no open port).
        sg = ec2.SecurityGroup(
            self, "InstanceSg", vpc=vpc, allow_all_outbound=True,
            description="health-checker box: HTTP in, all out",
        )
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP (Basic Auth gates it)")

        # ---- IAM role the EC2 box runs as ----------------------------------
        # boto3 in the backend container picks these credentials up automatically
        # via the instance metadata service — the same "default chain" that used the
        # local profile, so NO keys are stored anywhere.
        role = iam.Role(self, "InstanceRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"))
        # Shell access without an open SSH port:
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
        )
        # Pull the images CDK pushed to ECR:
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly")
        )
        # Read/write the conversations table (scoped to just this table):
        table.grant_read_write_data(role)
        # Read this environment's secrets from SSM (scoped to this env's prefix):
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{region}:{account}:parameter{ssm_prefix}/*"],
            )
        )
        # Decrypt the SecureString params (they use the default AWS-managed SSM key).
        role.add_to_policy(iam.PolicyStatement(actions=["kms:Decrypt"], resources=["*"]))

        # ---- Docker images: CDK builds them from the existing Dockerfiles ------
        # platform pinned to arm64 to match the t4g.micro (ARM). On deploy these are
        # pushed to the CDK assets ECR repo; the box pulls them in the boot script.
        backend_image = ecr_assets.DockerImageAsset(
            self, "BackendImage", directory=BACKEND_DIR, platform=ecr_assets.Platform.LINUX_ARM64
        )
        frontend_image = ecr_assets.DockerImageAsset(
            self, "FrontendImage", directory=FRONTEND_DIR, platform=ecr_assets.Platform.LINUX_ARM64
        )

        # ---- The boot script (cloud-init user-data) ------------------------
        # Runs once as root on first boot. NOTE: no `set -x` — that would echo the
        # fetched secrets into the instance log. Image URIs are baked in, so a code
        # change → new image hash → new script → CDK replaces the instance (see
        # user_data_causes_replacement below) → the new code goes live.
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euo pipefail",
            # swap: headroom on the 1 GB micro so memory spikes don't OOM-kill anything.
            "if [ ! -f /swapfile ]; then dd if=/dev/zero of=/swapfile bs=1M count=2048; "
            "chmod 600 /swapfile; mkswap /swapfile; swapon /swapfile; "
            "echo '/swapfile none swap sw 0 0' >> /etc/fstab; fi",
            # Docker.
            "dnf install -y docker",
            "systemctl enable --now docker",
            # Log in to ECR using the instance role's credentials.
            f"aws ecr get-login-password --region {region} | "
            f"docker login --username AWS --password-stdin {account}.dkr.ecr.{region}.amazonaws.com",
            # Fetch secrets from SSM (decrypted) into shell vars — not logged.
            f'ANTHROPIC_API_KEY="$(aws ssm get-parameter --name {ssm_prefix}/anthropic-api-key '
            f'--with-decryption --query Parameter.Value --output text --region {region})"',
            f'NCBI_API_KEY="$(aws ssm get-parameter --name {ssm_prefix}/ncbi-api-key '
            f'--with-decryption --query Parameter.Value --output text --region {region})"',
            f'BASIC_AUTH_PASSWORD="$(aws ssm get-parameter --name {ssm_prefix}/basic-auth-password '
            f'--with-decryption --query Parameter.Value --output text --region {region})"',
            # A shared Docker network so nginx can reach the backend by name.
            "docker network create app || true",
            "docker rm -f backend frontend || true",
            # Backend: no published port (only reachable inside the network). boto3
            # uses the instance role; AWS_REGION tells it where the table lives.
            "docker run -d --name backend --network app --restart unless-stopped "
            '-e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" '
            '-e NCBI_API_KEY="$NCBI_API_KEY" '
            f"-e DYNAMODB_TABLE_NAME={table_name} "
            f"-e AWS_REGION={region} "
            f"{backend_image.image_uri}",
            # Frontend: publishes :80; nginx resolves `backend` on the shared network.
            # BASIC_AUTH_* turn the password gate ON (see frontend/40-basic-auth.sh).
            "docker run -d --name frontend --network app --restart unless-stopped "
            "-p 80:80 "
            f"-e BASIC_AUTH_USER={basic_auth_user} "
            '-e BASIC_AUTH_PASSWORD="$BASIC_AUTH_PASSWORD" '
            f"{frontend_image.image_uri}",
        )

        # ---- The EC2 instance ----------------------------------------------
        instance = ec2.Instance(
            self,
            "Instance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType(instance_type),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64
            ),
            security_group=sg,
            role=role,
            user_data=user_data,
            # A change to the boot script (e.g. new image hash after a code change)
            # replaces the instance, so deploys actually roll out new code.
            user_data_causes_replacement=True,
        )
        # IMDS hop limit 2: Docker adds one network hop, and IMDSv2 defaults to a hop
        # limit of 1 — so without this, containers can't read the instance role creds.
        instance.node.default_child.add_property_override(
            "MetadataOptions", {"HttpTokens": "required", "HttpPutResponseHopLimit": 2}
        )

        # ---- Stable public address -----------------------------------------
        # An Elastic IP that survives instance replacement, so the URL doesn't change.
        eip = ec2.CfnEIP(self, "Eip")
        ec2.CfnEIPAssociation(
            self, "EipAssoc", allocation_id=eip.attr_allocation_id, instance_id=instance.instance_id
        )

        # ---- Outputs (printed after `cdk deploy`) --------------------------
        CfnOutput(self, "AppUrl", value=f"http://{eip.ref}", description="Open this in the browser")
        CfnOutput(self, "PublicIp", value=eip.ref)
        CfnOutput(self, "TableName", value=table_name)
        CfnOutput(self, "SsmPrefix", value=ssm_prefix, description="Where to put the secrets")
