# Copyright 2018 Tyler Caraza-Harter
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json, random

from lambda_framework import *

NET_ID_EMAIL_SUFFIX = '@wisc.edu'


# returns a string containing json
def get_roster_raw():
    response = s3().get_object(Bucket=BUCKET, Key='roster.json')
    return response['Body'].read().decode('utf-8')


def get_roster_net_ids():
    ids = []
    for row in json.loads(get_roster_raw()):
        if 'net_id' in row:
            ids.append(row['net_id'])
    return ids


# takes a dict to convert to json
def put_roster_raw(roster_dict):
    body = json.dumps(roster_dict, indent=2)
    s3().put_object(Bucket=BUCKET,
                    Key='roster.json',
                    Body=bytes(body, 'utf-8'),
                    ContentType='text/json',
    )
    return body


def roster_attach_user_raw(user_id, net_id):
    net_id = net_id.lower()

    path1 = 'users/google_to_net_id/%s.txt' % user_id
    if s3().path_exists(path1):
        return (500, 'google account already linked to NetID')

    path2 = 'users/net_id_to_google/%s.txt' % net_id
    if s3().path_exists(path2):
        return (500, 'NetID already linked to google account')

    # if only one of these succeeds, it will require manual cleanup in S3
    s3().put_object(Bucket=BUCKET,
                    Key=path1,
                    Body=bytes(net_id, 'utf-8'),
                    ContentType='text/json',
    )
    s3().put_object(Bucket=BUCKET,
                    Key=path2,
                    Body=bytes(user_id, 'utf-8'),
                    ContentType='text/json',
    )
    return (200, 'google account linked to NetID')


def net_id_to_google(net_id):
    net_id = net_id.lower()
    suffix = '@wisc.edu'
    if net_id.endswith(suffix):
        net_id = net_id[:-len(suffix)]
    try:
        response = s3().get_object(Bucket=BUCKET, Key='users/net_id_to_google/%s.txt' % net_id)
        return response['Body'].read().decode('utf-8')
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "NoSuchKey":
            return None
        raise e


def google_to_net_id(google_id):
    try:
        response = s3().get_object(Bucket=BUCKET, Key='users/google_to_net_id/%s.txt' % google_id)
        return response['Body'].read().decode('utf-8')
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "NoSuchKey":
            return None
        raise e


@route
@admin
def get_roster(user, event):
    return (200, {'roster': get_roster_raw()})


@route
@admin
def put_roster(user, event):
    put_roster_raw(json.loads(event['roster']))
    return (200, 'roster uploaded')


@route
@user
def get_net_id(user, event):
    user_id = user['sub']
    path = 'users/google_to_net_id/%s.txt' % user_id
    try:
        # extract net_id as plain text
        response = s3().get_object(Bucket=BUCKET, Key=path)
        net_id = response['Body'].read().decode('utf-8')
    except botocore.exceptions.ClientError as e:
        # not linked yet
        if e.response['Error']['Code'] == "NoSuchKey":
            # if student signed in with a net ID, we can link now
            if user['email'].lower().endswith(NET_ID_EMAIL_SUFFIX):
                net_id = user['email'][:-len(NET_ID_EMAIL_SUFFIX)]
                code, r = roster_attach_user_raw(user_id, net_id)
                if code != 200:
                    return (code, r)
                return (200, {"net_id": net_id})
            return (200, {"net_id": None})

        # some unexpected error
        raise e

    # verify it points back to this user_id
    try:
        response = s3().get_object(Bucket=BUCKET, Key='users/net_id_to_google/%s.txt' % net_id)
        user_id_check = response['Body'].read().decode('utf-8')
        if user_id != user_id_check:
            return (500, 'google/NetID linkage mismatch, please contact your instructor for help (1)')
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "NoSuchKey":
            return (500, 'google/NetID linkage mismatch, please contact your instructor for help (2)')
        # some unexpected error
        raise e

    return (200, {"net_id": net_id})


@route
@user
def roster_entry(user, event):
    email = user['email'].lower()
    parts = email.split("@")
    if parts[1] != "wisc.edu":
        return (500, 'not a wisc.edu email')
    net_id = parts[0]

    response = s3().get_object(Bucket=BUCKET, Key='roster.json')
    roster = json.loads(response['Body'].read().decode('utf-8'))
    roster = {row["net_id"]: row
              for row in roster
              if "net_id" in row}

    if not net_id in roster:
        return (500, "not on roster")

    if not roster[net_id]["enrolled"]:
        return (500, "not on enrolled")

    return (200, roster[net_id])


@route
@admin
def roster_merge_google_ids(user, event):
    '''
    Look at individual files that link NetIDs to google IDs and add
    that information to the main roster file for fast lookup.
    '''
    suffix = '.txt'
    all_files = s3_all_keys('users/net_id_to_google/')
    linked_users = {path.split('/')[-1][:-4]
                    for path in all_files
                    if path.endswith('.txt')}

    roster = json.loads(get_roster_raw())
    for student in roster:
        user_id = student.get('user_id', None)
        net_id = student.get('net_id', None)
        # see if there's a link file with data we could roll into the main roster
        if user_id == None and net_id in linked_users:
            path = 'users/net_id_to_google/%s.txt' % net_id
            response = s3().get_object(Bucket=BUCKET, Key=path)
            user_id = response['Body'].read().decode('utf-8')
            student['user_id'] = user_id
    body = put_roster_raw(roster)
    return (200, {'roster':body})