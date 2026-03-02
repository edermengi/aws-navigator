import argparse
import csv
import datetime
import functools
from dataclasses import asdict
from dataclasses import dataclass
from typing import Iterator, List, Dict

import boto3


@dataclass
class Globals:
    profile: str
    region: str
    account_id: str = None

    def __post_init__(self):
        sts = aws_client('sts', self.profile, self.region)
        self.account_id = sts.get_caller_identity()['Account']


_globals: Globals


@dataclass
class Resource:
    profile: str
    region: str
    type: str
    name: str


@functools.lru_cache
def session(profile_name: str):
    # Respect the requested AWS profile
    return boto3.Session(profile_name=profile_name) if profile_name else boto3.Session()


@functools.lru_cache
def aws_client(client: str, profile_name: str, region_name: str):
    return session(profile_name).client(client, region_name=region_name)


class ProgressLog:
    def __init__(self, res_tye: str):
        self.res_type = res_tye
        self.start = datetime.datetime.now().timestamp()
        self.count = 0

    def tick(self):
        self.count += 1
        if not self.count % 1000:
            print(f'Retrieved {self.count} {self.res_type} names so far...')

    def end(self):
        elapsed = datetime.datetime.now().timestamp() - self.start
        print(f'Retrieved {self.count} {self.res_type} names in {elapsed:.2f}s')


class ResourceProvider:
    def __init__(self,
                 res_type: str,
                 is_global: bool,
                 client: str,
                 list_func: str,
                 token_req: str,
                 token_resp: str,
                 items_prop: str,
                 name_mapper,
                 list_func_args: dict = None
                 ):
        self.res_type = res_type
        self.is_global = is_global
        self.name_mapper = name_mapper
        self.items_prop = items_prop
        self.token_resp = token_resp
        self.token_req = token_req
        self.list_func = list_func
        self.profile = _globals.profile
        self.region = _globals.region
        self.list_func_args = list_func_args or {}
        self.client = aws_client(client, self.profile, self.region)
        self.account_id = _globals.account_id

    def resources(self) -> Iterator[Resource]:
        progress = ProgressLog(self.res_type)
        token = None
        while True:
            req = {self.token_req: token} if token else {}
            list_function = getattr(self.client, self.list_func)
            resp = list_function(**{**req, **self.list_func_args})
            for item in resp.get(self.items_prop) or []:
                progress.tick()
                name = self.name_mapper(item)
                yield Resource(self.profile, self.region, self.res_type, name)
            token = resp.get(self.token_resp)
            if not token:
                progress.end()
                break


def tag(name, tags: List[Dict]):
    for _tag in tags or []:
        if _tag.get('Key') == name:
            return _tag.get('Value')
    return ''


# @formatter:off
_provider_args = [
    # res_type      global   client             list_function               req_token                    resp_token                  items                       name
    # _________       ___   __________         _____________________       ___________                  _____________             ___________                 ________________________________
    ('lambda',         0,   'lambda',          'list_functions',           'Marker',                   'NextMarker',             'Functions',                 lambda item: item['FunctionName']),
    ('loggroup',       0,   'logs',            'describe_log_groups',      'nextToken',                'nextToken',              'logGroups',                 lambda item: item['logGroupName']),
    ('secret',         0,   'secretsmanager',  'list_secrets',             'NextToken',                'NextToken',              'SecretList',                lambda item: item['Name']),
    ('bucket',         1,   's3',              'list_buckets',             'None',                     'None',                   'Buckets',                   lambda item: item['Name']),
    ('dynamodb',       0,   'dynamodb',        'list_tables',              'ExclusiveStartTableName',  'LastEvaluatedTableName', 'TableNames',                lambda item: item),
    ('rds-cluster',    0,   'rds',             'describe_db_clusters',     'Marker',                   'Marker',                 'DBClusters',                lambda item: item['DBClusterIdentifier']),
    ('rds-db',         0,   'rds',             'describe_db_instances',    'Marker',                   'Marker',                 'DBInstances',               lambda item: item['DBInstanceIdentifier']),
    ('security-group', 0,   'ec2',             'describe_security_groups', 'NextToken',                'NextToken',              'SecurityGroups',            lambda item: item['GroupId'] + "," + item["GroupName"]),
    ('elb',            0,   'elb',             'describe_load_balancers',  'Marker',                   'NextMarker',             'LoadBalancerDescriptions',  lambda item: item['LoadBalancerName']),
    ('elb-v2',         0,   'elbv2',           'describe_load_balancers',  'Marker',                   'NextMarker',             'LoadBalancers',             lambda item: item['LoadBalancerName']),
    ('sqs',            0,   'sqs',             'list_queues',              'NextToken',                'NextToken',              'QueueUrls',                 lambda item: item[item.rfind('/') + 1:]),
    ('sns',            0,   'sns',             'list_topics',              'NextToken',                'NextToken',              'Topics',                    lambda item: item['TopicArn'][item['TopicArn'].rfind(':') + 1:]),
    ('api',            0,   'apigateway',      'get_rest_apis',            'position',                 'position',               'items',                     lambda item: item['id'] + "," + item['name']),
    ('api-v2',         0,   'apigatewayv2',    'get_apis',                 'NextToken',                'NextToken',              'Items',                     lambda item: item['ApiId'] + "," + item['Name']),
    ('web-acl',        0,   'wafv2',           'list_web_acls',            'NextMarker',               'NextMarker',             'WebACLs',                   lambda item: item['Name'] + "," + item['Id'],                           {'Scope': 'REGIONAL'}),
    ('waf-ip-set',     0,   'wafv2',           'list_ip_sets',             'NextMarker',               'NextMarker',             'IPSets',                    lambda item: item['Name'] + "," + item['Id'],                           {'Scope': 'REGIONAL'}),
    ('codebuild',      0,   'codebuild',       'list_projects',            'nextToken',                'nextToken',              'projects',                  lambda item: item + ',' + _globals.account_id),
    ('codepipeline',   0,   'codepipeline',    'list_pipelines',           'nextToken',                'nextToken',              'pipelines',                 lambda item: item['name']),
    ('subnet',         0,   'ec2',             'describe_subnets',         'NextToken',                'NextToken',              'Subnets',                   lambda item: item['SubnetId'] + "," + tag('Name', item.get('Tags'))),
    ('ec2',            0,   'ec2',             'describe_instances',       'NextToken',                'NextToken',              'Reservations',              lambda item: item['Instances'][0]['InstanceId'] + "," + tag('Name', item['Instances'][0].get('Tags'))),
    ('role',           1,   'iam',             'list_roles',               'Marker',                   'Marker',                 'Roles',                     lambda item: item['RoleName']),
    ('iam-user',       1,   'iam',             'list_users',               'Marker',                   'Marker',                 'Users',                     lambda item: item['UserName']),
    ('iam-group',      1,   'iam',             'list_groups',              'Marker',                   'Marker',                 'Groups',                    lambda item: item['GroupName']),
    ('iam-policy',     1,   'iam',             'list_policies',            'Marker',                   'Marker',                 'Policies',                  lambda item: item['PolicyName'],                                        {'Scope': 'Local'}),
    ('iam-instance-profile', 1, 'iam',         'list_instance_profiles',   'Marker',                   'Marker',                 'InstanceProfiles',          lambda item: item['InstanceProfileName']),
    ('event-rule',     0,   'events',          'list_rules',               'NextToken',                'NextToken',              'Rules',                     lambda item: item['Name']),
    # Networking core resources
    ('vpc',            0,   'ec2',             'describe_vpcs',            'NextToken',                'NextToken',              'Vpcs',                      lambda item: item['VpcId'] + "," + tag('Name', item.get('Tags'))),
    ('route-table',    0,   'ec2',             'describe_route_tables',    'NextToken',                'NextToken',              'RouteTables',               lambda item: item['RouteTableId'] + "," + tag('Name', item.get('Tags'))),
    ('internet-gw',    0,   'ec2',             'describe_internet_gateways','NextToken',               'NextToken',              'InternetGateways',          lambda item: item['InternetGatewayId']),
    ('network-acl',    0,   'ec2',             'describe_network_acls',    'NextToken',                'NextToken',              'NetworkAcls',               lambda item: item['NetworkAclId']),
    # EKS clusters
    ('eks-cluster',    0,   'eks',             'list_clusters',            'nextToken',                'nextToken',              'clusters',                  lambda item: item),

    # Additional core resources (~20)
    # EC2 / VPC primitives
    ('volume',         0,   'ec2',             'describe_volumes',         'NextToken',                'NextToken',              'Volumes',                   lambda item: item['VolumeId'] + "," + tag('Name', item.get('Tags'))),
    ('eni',            0,   'ec2',             'describe_network_interfaces','NextToken',              'NextToken',              'NetworkInterfaces',         lambda item: item['NetworkInterfaceId'] + "," + tag('Name', item.get('TagSet'))),
    ('eip',            0,   'ec2',             'describe_addresses',       'NextToken',                'NextToken',              'Addresses',                 lambda item: (item.get('AllocationId') or '') + "," + (item.get('PublicIp') or '')),
    ('nat-gw',         0,   'ec2',             'describe_nat_gateways',    'NextToken',                'NextToken',              'NatGateways',               lambda item: item['NatGatewayId']),
    ('vpc-endpoint',   0,   'ec2',             'describe_vpc_endpoints',   'NextToken',                'NextToken',              'VpcEndpoints',              lambda item: item['VpcEndpointId'] + "," + (item.get('ServiceName') or '')),
    ('vpc-peering',    0,   'ec2',             'describe_vpc_peering_connections','NextToken',          'NextToken',              'VpcPeeringConnections',     lambda item: item['VpcPeeringConnectionId']),
    ('tgw',            0,   'ec2',             'describe_transit_gateways','NextToken',                'NextToken',              'TransitGateways',           lambda item: item['TransitGatewayId'] + "," + tag('Name', item.get('Tags'))),
    ('vpn-gw',         0,   'ec2',             'describe_vpn_gateways',    'NextToken',                'NextToken',              'VpnGateways',               lambda item: item['VpnGatewayId']),
    ('customer-gw',    0,   'ec2',             'describe_customer_gateways','NextToken',               'NextToken',              'CustomerGateways',          lambda item: item['CustomerGatewayId'] + "," + (item.get('IpAddress') or '')),
    ('prefix-list',    0,   'ec2',             'describe_managed_prefix_lists','NextToken',            'NextToken',              'PrefixLists',               lambda item: item['PrefixListId'] + "," + (item.get('PrefixListName') or '')),

    # Scaling / containers / registries
    ('autoscaling-group',0, 'autoscaling',     'describe_auto_scaling_groups','NextToken',            'NextToken',              'AutoScalingGroups',         lambda item: item['AutoScalingGroupName']),
    ('ecr-repo',       0,   'ecr',             'describe_repositories',    'nextToken',                'nextToken',              'repositories',              lambda item: item['repositoryName']),
    ('ecs-cluster',    0,   'ecs',             'list_clusters',            'nextToken',                'nextToken',              'clusterArns',               lambda item: item.split('/')[-1]),

    # Ops / config / storage
    ('cloudwatch-alarm',0,  'cloudwatch',      'describe_alarms',          'NextToken',                'NextToken',              'MetricAlarms',              lambda item: item['AlarmName']),
    ('acm-cert',       0,   'acm',             'list_certificates',        'NextToken',                'NextToken',              'CertificateSummaryList',    lambda item: (item.get('CertificateArn') or '') + "," + (item.get('DomainName') or '')),
    ('route53-zone',   1,   'route53',         'list_hosted_zones',        'Marker',                   'NextMarker',             'HostedZones',               lambda item: (item.get('Id') or '').split('/')[-1] + "," + (item.get('Name') or '')),
    ('kms-key',        0,   'kms',             'list_keys',                'Marker',                   'NextMarker',             'Keys',                      lambda item: (item.get('KeyId') or '') + "," + (item.get('KeyArn') or '')),
    ('ssm-param',      0,   'ssm',             'describe_parameters',      'NextToken',                'NextToken',              'Parameters',                lambda item: item.get('Name') or ''),
    ('efs',            0,   'efs',             'describe_file_systems',    'Marker',                   'NextMarker',             'FileSystems',               lambda item: (item.get('FileSystemId') or '')),
    ('cfn-stack',      0,   'cloudformation',  'describe_stacks',          'NextToken',                'NextToken',              'Stacks',                    lambda item: item.get('StackName') or ''),
]
# @formatter:on


def _all_providers() -> List[ResourceProvider]:
    return [ResourceProvider(*_args) for _args in _provider_args]


def _parse_args():
    parser = argparse.ArgumentParser(description='Store AWS resources names to CSV file')
    parser.add_argument('--profiles', type=str, required=True, nargs='+', help='AWS profiles')
    parser.add_argument('--regions', type=str, required=True, nargs='+', help="AWS regions")
    parser.add_argument('--output-file', type=str, required=True, help='Output CSV file')
    parser.add_argument('--types', type=str, required=False, nargs='+', help='Filter resource types (lambda, ec2, ..)')
    return parser.parse_args()


if __name__ == '__main__':
    args = _parse_args()
    profiles = args.profiles
    regions = args.regions
    types = args.types
    output_file = args.output_file

    with open(output_file, 'a', newline='') as f:
        fieldnames = ['profile', 'region', 'type', 'name']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        for profile in profiles:
            for rno, region in enumerate(regions):
                print(f'Processing AWS profile [{profile}] and region [{region}]')
                _globals = Globals(profile, region)

                for provider in _all_providers():
                    # include global resources only once
                    if rno and provider.is_global:
                        continue
                    if types and provider.res_type not in types:
                        continue
                    for res in provider.resources():
                        writer.writerow(asdict(res))
