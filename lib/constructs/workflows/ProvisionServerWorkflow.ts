import { Construct, Duration } from "monocdk";
import { LayerVersion } from "monocdk/aws-lambda";
import {
  StateMachine,
  Succeed,
  Fail,
  InputType
} from "monocdk/aws-stepfunctions";
import { LambdaInvoke, SqsSendMessage } from "monocdk/aws-stepfunctions-tasks";
import { PolicyStatement } from "monocdk/aws-iam";
import { Table } from "monocdk/aws-dynamodb";
import { PythonLambda } from "../PythonLambdaConstruct"
import { Bucket } from "monocdk/aws-s3";
import { Queue } from "monocdk/aws-sqs";

export interface ProvisionServerProps {
    readonly discordLayer: LayerVersion
    readonly serverboiUtilsLayer: LayerVersion
    readonly tokenBucket: Bucket
    readonly tokenQueue: Queue
    readonly serverList: Table
    readonly userList: Table
}

export class ProvisionServerWorkflow extends Construct {

  public readonly provisionWorkflow: StateMachine
  public readonly terminationWorkflow: StateMachine

  constructor(scope: Construct, id: string, props: ProvisionServerProps) {
    super(scope, id);

    const provisionName = "Provision-And-Create-Resources-Lambda";
    const provision = new PythonLambda(this, provisionName, {
      name: provisionName,
      codePath: "lambdas/handlers/verify_and_provision_lambda/",
      handler: "verify_and_provision.main.lambda_handler",
      layers: [props.discordLayer, props.serverboiUtilsLayer],
      environment:{
          TOKEN_BUCKET: props.tokenBucket.bucketName,
          USER_TABLE: props.userList.tableName,
          SERVER_TABLE: props.serverList.tableName,
      },
    })
    provision.lambda.addToRolePolicy(
      new PolicyStatement({
        resources: ["*"],
        actions: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "dynamodb:Scan",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "sts:AssumeRole",
        ],
      })
    );

    const rollbackName = 'Rollback-Resources'
    const rollback = new PythonLambda(this, rollbackName, {
      name: rollbackName,
      codePath: "lambdas/handlers/rollback_provision",
      handler: "rollback_provision.main.lambda_handler",
      layers: [props.discordLayer, props.serverboiUtilsLayer],
      environment:{
          USER_TABLE: props.userList.tableName,
          SERVER_TABLE: props.serverList.tableName,
      },
    })
    provision.lambda.addToRolePolicy(
      new PolicyStatement({
        resources: ["*"],
        actions: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "ec2:TerminateInstances",
          "dynamodb:DeleteItem",
          "sts:AssumeRole",
        ],
      })
    );

    //step definitions
    const createResources = new LambdaInvoke(
      this,
      "Create-Resources-Step",
      {
        lambdaFunction: provision.lambda,
        outputPath: '$.Payload'
      }
    );

    const waitForBootstrap = new SqsSendMessage(this, "Wait-For-Bootstrap", {
      messageBody: {
        type: InputType.OBJECT,
        value: {
          "Input.$": "$",
          "TaskToken.$": "$$.Task.Token"
        }
      },
      
      queue: props.tokenQueue,
      timeout: Duration.hours(1),
    })

    const rollbackProvision = new LambdaInvoke(this, "Rollback-Provision", {
      lambdaFunction: rollback.lambda,
      inputPath: '$.Payload'
    })

    const errorStep = new Fail(this, 'Fail-Step')

    const endStep = new Succeed(this, 'End-Step')

    const stepDefinition = createResources
      .next(waitForBootstrap)

      waitForBootstrap.next(endStep)

      waitForBootstrap.addCatch(rollbackProvision)

      rollbackProvision.next(errorStep)

    this.provisionWorkflow = new StateMachine(this, "Provision-Server-State-Machine", {
      definition: stepDefinition,
      stateMachineName: "Provision-Server-Workflow",
    });

  }
}