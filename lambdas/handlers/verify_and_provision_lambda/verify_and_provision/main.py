import boto3
import json
from uuid import uuid4
import os
from botocore.exceptions import ClientError as BotoClientError
from boto3.dynamodb.conditions import Key
import serverboi_utils.embeds as embed_utils
import serverboi_utils.responses as response_utils
import verify_and_provision.lib.docker_game_commands as docker_commands
from discord import Color

DYNAMO = boto3.resource("dynamodb")
STS = boto3.client("sts")

USER_TABLE = DYNAMO.Table(os.environ.get("USER_TABLE"))
SERVER_TABLE = DYNAMO.Table(os.environ.get("SERVER_TABLE"))

WORKFLOW_NAME = "Provision-Server"
STAGE = "Provision"


def _get_user_info_from_table(user_id: str, table: boto3.resource) -> str:
    try:
        response = USER_TABLE.query(KeyConditionExpression=Key("UserID").eq(user_id))
    except BotoClientError as error:
        print(error)

    if len(response["Items"]) != 1:
        raise Exception(f"More than one user with id of {user_id}")
    else:
        return response["Items"][0]


def form_user_data(docker_command: str) -> str:
    return f"""#!/bin/bash
sudo apt-get update && sudo apt-get upgrade -y

sudo apt-get install \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release -y

curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

echo \
  "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update

sudo apt-get install docker-ce docker-ce-cli containerd.io -y

{docker_command}"""


def get_image_id(ec2: boto3.client, region: str) -> str:
    images = ec2.describe_images(
        Filters=[
            {"Name": "description", "Values": ["Debian 10 (20210329-591)"]},
            {"Name": "architecture", "Values": ["x86_64"]},
            {"Name": "virtualization-type", "Values": ["hvm"]},
        ],
        Owners=["136693071363"],
    )

    return images["Images"][0]["ImageId"]


def lambda_handler(event, context) -> dict:
    game = event["game"]
    name = event["name"]
    region = event["region"]
    user_id = event["user_id"]
    username = event["username"]
    password = event["password"]
    service = event["service"]
    interaction_token = event["interaction_token"]
    application_id = event["application_id"]
    execution_name = event["execution_name"]

    # pack event into a dict cause lambda flips if you try to **event -_-
    kwargs = {}
    for item, value in event.items():
        kwargs[item] = value

    embed = embed_utils.form_workflow_embed(
        workflow_name=WORKFLOW_NAME,
        workflow_description=f"Workflow ID: {execution_name}",
        status="🟢 running",
        stage=STAGE,
        color=Color.green(),
    )

    data = response_utils.form_response_data(embeds=[embed])
    response_utils.edit_response(application_id, interaction_token, data)

    server_id = uuid4()
    server_id = str(server_id)[:4].upper()

    event["server_id"] = server_id

    user_info = _get_user_info_from_table(user_id, USER_TABLE)

    account_id = user_info.get("AWSAccountID")
    event["account_id"] = account_id

    if account_id:

        try:
            assumed_role = STS.assume_role(
                RoleArn=f"arn:aws:iam::{account_id}:role/ServerBoi-Resource.Assumed-Role",
                RoleSessionName="ServerBoiValidateAWSAccount",
            )

            # Replace with boto session then creation
            ec2_client = boto3.client(
                "ec2",
                region_name=region,
                aws_access_key_id=assumed_role["Credentials"]["AccessKeyId"],
                aws_secret_access_key=assumed_role["Credentials"]["SecretAccessKey"],
                aws_session_token=assumed_role["Credentials"]["SessionToken"],
            )

            ec2_resource = boto3.resource(
                "ec2",
                region_name=region,
                aws_access_key_id=assumed_role["Credentials"]["AccessKeyId"],
                aws_secret_access_key=assumed_role["Credentials"]["SecretAccessKey"],
                aws_session_token=assumed_role["Credentials"]["SessionToken"],
            )

            print("Account verified")

        except BotoClientError as error:
            print(error)

        with open("verify_and_provision/build.json") as build:
            build_data = json.load(build)

        game_data = build_data[game]

        event["wait_time"] = game_data["build_time"]
        port_range = game_data["ports"]
        event["server_port"] = port_range[0]

        sec_group_name = f"ServerBoi-Resource-{game}-{name}-{server_id}"

        sec_resp = ec2_client.create_security_group(
            Description=f"Sec group for {game} server: {name}", GroupName=sec_group_name
        )

        group_id = sec_resp["GroupId"]

        ec2_client.authorize_security_group_egress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 0,
                    "ToPort": 65535,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
                {
                    "IpProtocol": "udp",
                    "FromPort": 0,
                    "ToPort": 65535,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
            ],
        )

        ec2_client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": port_range[0],
                    "ToPort": port_range[1],
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
                {
                    "IpProtocol": "udp",
                    "FromPort": port_range[0],
                    "ToPort": port_range[1],
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
            ],
        )

        docker_command = docker_commands.route_docker_command(game, **kwargs)
        user_data = form_user_data(docker_command)

        image_id = get_image_id(ec2_client, region)

        instances = ec2_resource.create_instances(
            ImageId=image_id,
            InstanceType=game_data["aws"]["instance_type"],
            MaxCount=1,
            MinCount=1,
            SecurityGroupIds=[group_id],
            UserData=user_data,
        )

        instance = instances[0]

        instance.create_tags(
            Tags=[
                {"Key": "ManagedBy", "Value": "ServerBoi"},
            ]
        )
        instance_id = instance.instance_id

        server_item = {
            "ServerID": server_id,
            "OwnerID": user_id,
            "Owner": username,
            "Game": game,
            "ServerName": name,
            "Password": password,
            "Service": service,
            "AccountID": account_id,
            "Region": region,
            "InstanceID": instance_id,
            "Port": port_range[0],
        }

        SERVER_TABLE.put_item(Item=server_item)

        ip = instance.public_ip_address
        print(f"Instance IP: {ip}")

        if ip != "":
            event["instance_ip"] = ip

        event["instance_id"] = instance_id

        return event

    else:

        embed = embed_utils.form_workflow_embed(
            workflow_name=WORKFLOW_NAME,
            workflow_description=f"Workflow ID: {execution_name}",
            status="❌ failed",
            stage=STAGE,
            color=Color.red(),
        )

        data = response_utils.form_response_data(embeds=[embed])
        response_utils.edit_response(application_id, interaction_token, data)

        raise Exception("No account associated with user")
