#!/usr/bin/env python3
"""CDK app entry point.

Running `cdk synth` / `cdk deploy` executes THIS file (see cdk.json's "app" line).
It creates the CDK `App` and instantiates our stack(s). A "stack" becomes one
CloudFormation stack in AWS — a group of resources created/updated/deleted together.

The headline idea of IaC lives here: dev and prod are the SAME stack code,
instantiated twice with different arguments. prod is not a separate hand-built
setup — it's a second copy of a proven definition.
"""
import aws_cdk as cdk

from health_checker_stack import HealthCheckerStack

app = cdk.App()

# Account + region pinned to the PERSONAL account on purpose (this machine also has a
# WORK profile) — so neither environment can be deployed to the wrong account.
AWS_ENV = cdk.Environment(account="480566308626", region="us-east-1")

# Two independent environments from one stack definition, differing only by these args.
HealthCheckerStack(app, "HealthChecker-dev", env_name="dev", instance_type="t4g.micro", env=AWS_ENV)
HealthCheckerStack(app, "HealthChecker-prod", env_name="prod", instance_type="t4g.micro", env=AWS_ENV)

app.synth()
