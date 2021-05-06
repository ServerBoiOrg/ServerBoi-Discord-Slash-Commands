import { Stack, Construct } from "monocdk";
import { Table, AttributeType, Attribute } from "monocdk/aws-dynamodb";
import { Bucket } from "monocdk/aws-s3";
import { BucketDeployment, Source } from "monocdk/aws-s3-deployment";
import { OnboardingConstruct } from "./OnboardCfnDeployment";
import { SynthUtils } from "@monocdk-experiment/assert";

export class ServerlessBoiResourcesStack extends Stack {
  //Creates all resources referenced across stacks

  readonly resourcesBucket: Bucket;
  readonly serverList: Table;
  readonly userList: Table;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    this.resourcesBucket = new Bucket(this, "Resources-Bucket", {
      bucketName: "serverboi-resources-bucket",
      publicReadAccess: true,
    });

    const deployment = new BucketDeployment(this, "Bucket-Deployment", {
      sources: [Source.asset("lib/stacks/resources/onboardingDeployment")],
      destinationBucket: this.resourcesBucket,
    });

    this.serverList = new Table(this, "Server-Tarle", {
      partitionKey: { name: "ServerID", type: AttributeType.STRING },
      tableName: "ServerBoi-Server-List",
    });

    this.userList = new Table(this, "User-Table", {
      partitionKey: { name: "UserID", type: AttributeType.STRING },
      tableName: "ServerBoi-User-List",
    });
  }
}