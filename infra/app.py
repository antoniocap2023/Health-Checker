#!/usr/bin/env python3
"""CDK app entry point.

Running `cdk synth` / `cdk deploy` executes THIS file (see cdk.json's "app" line).
It creates the CDK `App` and instantiates our stack(s). A "stack" becomes one
CloudFormation stack in AWS — a group of resources created/updated/deleted together.

The headline idea of IaC lives here: dev and prod are the SAME stack code,
instantiated twice with different arguments. prod is not a separate hand-built
setup — it's a second copy of a proven definition.
"""
import os

import aws_cdk as cdk

from cicd_stack import CicdStack
from health_checker_stack import HealthCheckerStack

app = cdk.App()

# Account + region come from the environment, so no account id is hardcoded in the repo.
# CDK sets CDK_DEFAULT_ACCOUNT/REGION from the active credentials at synth time; set
# AWS_ACCOUNT_ID explicitly to pin deploys to one account (guards against deploying to
# the wrong profile).
AWS_ENV = cdk.Environment(
    account=os.environ.get("AWS_ACCOUNT_ID") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION") or "us-east-1",
)

# Two independent environments from one stack definition, differing only by these args.
HealthCheckerStack(app, "HealthChecker-dev", env_name="dev", instance_type="t4g.micro", env=AWS_ENV)
HealthCheckerStack(app, "HealthChecker-prod", env_name="prod", instance_type="t4g.micro", env=AWS_ENV)

# Account-global, one-time: the GitHub OIDC trust + deploy role that lets CI run the
# deploys above (and the weekly eval loop) without long-lived AWS keys.
CicdStack(app, "HealthChecker-cicd", env=AWS_ENV)

app.synth()
