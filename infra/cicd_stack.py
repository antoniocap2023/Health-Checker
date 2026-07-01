"""CI/CD trust stack — lets GitHub Actions deploy via OIDC, with NO long-lived AWS keys.

Deployed ONCE from a laptop (`cdk deploy HealthChecker-cicd`). Thereafter GitHub Actions
assumes the created role via OIDC to run `cdk deploy` (dev/prod) and the weekly eval loop.

Creates (account-wide, one-time):
  - a GitHub OIDC identity provider (token.actions.githubusercontent.com)
  - a deploy role, trust-scoped to THIS repo, allowed to (a) assume the CDK bootstrap
    roles — that's how `cdk deploy` gets its real permissions — and (b) read/write the
    eval DynamoDB table, so the Phase-8 loop can populate/score from CI.

Kept separate from HealthCheckerStack because it's account-global and one-time, not
per-environment.
"""
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_iam as iam,
)
from constructs import Construct

# The GitHub repo whose Actions may assume the role (the OIDC `sub` claim scope).
GITHUB_OWNER_REPO = "antoniocap2023/Health-Checker"
EVAL_TABLE = "health-checker-eval-conversations"


class CicdStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 github_owner_repo: str = GITHUB_OWNER_REPO, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # GitHub's OIDC provider — one per account per URL. Audience is the AWS STS
        # audience GitHub mints tokens for.
        provider = iam.OpenIdConnectProvider(
            self, "GitHubOidcProvider",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )

        # The role GitHub Actions assumes. Trust is scoped to this repo (any branch/env);
        # tighten the `sub` StringLike to `...:ref:refs/heads/main` or `...:environment:prod`
        # if you want per-workflow scoping later.
        deploy_role = iam.Role(
            self, "GitHubDeployRole",
            role_name="github-actions-deploy",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": f"repo:{github_owner_repo}:*",
                    },
                },
            ),
        )

        # `cdk deploy` works by assuming the account's CDK bootstrap roles (deploy,
        # file-publishing, image-publishing, lookup). Granting assume on `cdk-*` is the
        # standard, least-broad way to let CI deploy without account-admin rights.
        deploy_role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-*"],
        ))

        # The Phase-8 weekly loop runs populate/check against the eval DynamoDB table.
        deploy_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem",
                "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem",
                "dynamodb:BatchGetItem", "dynamodb:DescribeTable",
            ],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{EVAL_TABLE}",
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{EVAL_TABLE}/index/*",
            ],
        ))

        CfnOutput(self, "DeployRoleArn", value=deploy_role.role_arn)
        CfnOutput(self, "OidcProviderArn", value=provider.open_id_connect_provider_arn)
