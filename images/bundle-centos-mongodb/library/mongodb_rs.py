#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2014, George Miroshnykov <george.miroshnykov@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: mongodb_rs
version_added: "1.5"
short_description: Initiate, add and remove members from MongoDB replica set
description:
   - 'Initiate, add and remove members from MongoDB replica set.
     See: http://docs.mongodb.org/manual/reference/replica-configuration/'
options:
    login_user:
        description:
            - The username used to authenticate with
        required: false
        default: null
    login_password:
        description:
            - The password used to authenticate with
        required: false
        default: null
    login_host:
        description:
            - The host running the database
        required: false
        default: localhost
    login_port:
        description:
            - The port to connect to
        required: false
        default: 6379
    member:
        description:
            - The host[:port] to add/remove from a replica set.
        required: false
        default: null
    arbiter_only:
        description:
            - Should a new member be added as arbiter.
        required: false
        default: false
    build_indexes:
        description:
            - Determines whether the mongod builds indexes on this member.
              Do not set to false for instances that receive queries from clients.
        required: false
        default: true
    hidden:
        description:
            - When this value is true, the replica set hides this instance,
              and does not include the member in the output of db.isMaster()
              or isMaster.
              This prevents read operations (i.e. queries) from ever reaching
              this host by way of secondary read preference.
        required: false
        default: false
    priority:
        description:
            - Specify higher values to make a member more eligible to become
              primary, and lower values to make the member less eligible to
              become primary.
              Priorities are only used in comparison to each other.
              Members of the set will veto election requests from members
              when another eligible member has a higher priority value.
              Changing the balance of priority in a replica set will trigger
              an election.
        required: false
        default: 1.0
    slave_delay:
        description:
            - Describes the number of seconds "behind" the primary that this
              replica set member should "lag."
              Use this option to create delayed members, that maintain a copy
              of the data that reflects the state of the data set at some
              amount of time in the past, specified in seconds.
              Typically such delayed members help protect against human error,
              and provide some measure of insurance against the unforeseen
              consequences of changes and updates.
        required: false
        default: 0
    votes:
        description:
            - Controls the number of votes a server will cast in a
              replica set election.
              The number of votes each member has can be any non-negative integer,
              but it is highly recommended each member has 1 or 0 votes.
        required: false
        default: 1
    state:
        description:
            - The desired state of the replica set
        required: true
        default: null
        choices: [ "initiated", "present", "absent" ]
notes:
    - See also M(mongodb_user)
requirements: [ pymongo ]
author: George Miroshnykov <george.miroshnykov@gmail.com>
'''

EXAMPLES = '''
# initiate a replica set
- mongodb_rs: state=initiated

# add a replica set member
- mongodb_rs: member=secondary.example.com state=present

# add an arbiter on custom port
- mongodb_rs: member=arbiter.example.com:30000 arbiter_only=yes state=present

# remove a replica set member
- mongodb_rs: member=secondary.example.com state=absent

# use all possible parameters when adding a member (please don't do that in production):
- mongodb_rs: >
    member=secondary.example.com
    state=present
    arbiter_only=yes
    build_indexes=no
    hidden=yes
    priority=0
    slave_delay=3600
    votes=42
'''

DEFAULT_PORT = 27017

import time

pymongo_found = False
try:
    from pymongo.errors import ConnectionFailure
    from pymongo.errors import OperationFailure
    from pymongo.errors import AutoReconnect
    from pymongo import MongoClient
    pymongo_found = True
except ImportError:
    try:  # for older PyMongo 2.2
        from pymongo import Connection as MongoClient
        pymongo_found = True
    except ImportError:
        pass

def normalize_member_host(member_host):
    if ':' not in member_host:
        member_host = member_host + ':' + str(DEFAULT_PORT)
    return member_host

def create_member(host, **kwargs):
    member = dict(host = host)

    if kwargs['arbiter_only']:
        member['arbiterOnly'] = True

    if not kwargs['build_indexes']:
        member['buildIndexes'] = False

    if kwargs['hidden']:
        member['hidden'] = True

    if kwargs['priority'] != 1.0:
        member['priority'] = kwargs['priority']

    if kwargs['slave_delay'] != 0:
        member['slaveDelay'] = kwargs['slave_delay']

    if kwargs['votes'] != 1:
        member['votes'] = kwargs['votes']

    return member

def authenticate(client, login_user, login_password):
    try:
        client.admin.authenticate(login_user, login_password)
    except OperationFailure:
        pass

def rs_is_master(client):
    return client.local.command('isMaster')

def rs_get_config(client):
    return client.local.system.replset.find_one()

def rs_initiate(client, rs_config = None):
    if rs_config is None:
        client.admin.command('replSetInitiate')
    else:
        client.admin.command('replSetInitiate', rs_config)

def rs_get_member(rs_config, member):
    a = filter(lambda x: x['host'] == member, rs_config['members'])
    return a[0] if a else None

def rs_get_member_from_status(rs_status, member):
    a = filter(lambda x: x['name'] == member, rs_status['members'])
    return a[0] if a else None

def rs_get_next_member_id(rs_config):
    if rs_config is None or rs_config['members'] is None:
        return 0

    def compare_max_id(max_id, current_member):
        id = int(current_member['_id'])
        return id if id > max_id else max_id

    max_id = reduce(compare_max_id, rs_config['members'], 0)
    return max_id + 1

def rs_add_member(rs_config, member):
    rs_config['members'].append(member)
    rs_config['version'] = rs_config['version'] + 1
    return rs_config

def rs_remove_member(rs_config, member):
    for i, candidate in enumerate(rs_config['members']):
        if candidate['host'] == member['host']:
            del rs_config['members'][i]
            break

    rs_config['version'] = rs_config['version'] + 1
    return rs_config

def rs_reconfigure(client, rs_config):
    try:
        client.admin.command('replSetReconfig', rs_config)
    except AutoReconnect:
        pass

def rs_wait_for_ok_and_primary(client, timeout = 60):
    while True:
        status = client.admin.command('replSetGetStatus', check=False)
        if status['ok'] == 1 and status['myState'] == 1:
            return

        timeout = timeout - 1
        if timeout == 0:
            raise Exception('reached timeout while waiting for rs.status() to become ok=1')

        time.sleep(1)

def rs_wait_for_ok_and_secondary(client, secondary, timeout = 60):
    while True:
        status = client.admin.command('replSetGetStatus', check=False)
        secondary_status = rs_get_member_from_status(status, secondary)
        if status['ok'] == 1 and secondary_status['health'] == 1 and secondary_status['state'] in [1,2,7]:
            return

        timeout = timeout - 1
        if timeout == 0:
            raise Exception('reached timeout while waiting for rs.status() to become ok=1')

        time.sleep(1)

def member_is_alive(host, port, timeout = 60):
    while True:
        try:
            client = MongoClient(host, port, connectTimeoutMS=500)
            client.close()
            return True
        except ConnectionFailure:
            pass

        timeout = timeout - 1
        if timeout == 0:
            raise Exception('reached timeout while waiting for a member to become alive')

        time.sleep(1)

def main():
    module = AnsibleModule(
        argument_spec = dict(
            login_host      = dict(default='localhost'),
            login_port      = dict(type='int', default=DEFAULT_PORT),
            login_user      = dict(default=None),
            login_password  = dict(default=None),
            replica_set     = dict(default=None),
            ssl             = dict(default=False),
            member          = dict(default=None),
            arbiter_only    = dict(type='bool', choices=BOOLEANS, default='no'),
            build_indexes   = dict(type='bool', choices=BOOLEANS, default='yes'),
            hidden          = dict(type='bool', choices=BOOLEANS, default='no'),
            priority        = dict(default='1.0'),
            slave_delay     = dict(type='int', default='0'),
            votes           = dict(type='int', default='1'),
            state           = dict(required=True, choices=['initiated', 'present', 'absent']),
            timeout         = dict(type='int', default='60'),
        )
    )

    if not pymongo_found:
        module.fail_json(msg='the python pymongo module is required')

    login_host      = module.params['login_host']
    login_port      = module.params['login_port']
    login_user      = module.params['login_user']
    login_password  = module.params['login_password']
    replica_set     = module.params['replica_set']
    ssl             = module.params['ssl']
    member_host     = module.params['member']
    state           = module.params['state']
    timeout         = module.params['timeout']

    if member_host is not None:
        member_host = normalize_member_host(member_host)

    member = create_member(
        host            = member_host,
        arbiter_only    = module.params['arbiter_only'],
        build_indexes   = module.params['build_indexes'],
        hidden          = module.params['hidden'],
        priority        = float(module.params['priority']),
        slave_delay     = module.params['slave_delay'],
        votes           = module.params['votes']
    )

    result = dict(changed=False)

    # connect
    client = None
    try:
        if replica_set is None:
            client = MongoClient(login_host, login_port, ssl=ssl)
        else:
            client = MongoClient(login_host, login_port, replicaSet=replica_set, ssl=ssl)
    except ConnectionFailure as e:
        module.fail_json(msg='unable to connect to database: %s' % e)

    # authenticate
    if login_user is not None and login_password is not None:
        authenticate(client, login_user, login_password)

    if state == 'initiated':
        # initiate only if not configured yet
        is_master = rs_is_master(client)
        if 'setName' not in is_master:
            if member_host is None:
                rs_initiate(client)
            else:
                if replica_set is None:
                    module.fail_json(msg='replica_set must be specified when host is specified on state=initiated')
                rs_config = {
                    "_id": replica_set,
                    "members": [member]
                }
                rs_config['members'][0]['_id'] = 0
                rs_initiate(client, rs_config)
            rs_wait_for_ok_and_primary(client)
            result['changed'] = True
    else:
        # get replica set config
        rs_config = rs_get_config(client)
        # Set the id of the member
        member['_id'] = rs_get_next_member_id(rs_config)
        # check if given host is currently a member of replica set
        current_member = rs_get_member(rs_config, member['host'])

        if state == 'present' and current_member is None:
            while True:
                try:
                    # check if the given host is currently running (mongod started and listening)
                    if member_is_alive(member['host'].split(':')[0], int(member['host'].split(':')[1])):
                        rs_config = rs_add_member(rs_config, member)
                        rs_reconfigure(client, rs_config)
                        rs_wait_for_ok_and_secondary(client, member['host'])
                        result['changed'] = True
                        break
                except OperationFailure as e:
                    rs_config = rs_get_config(client)
                    member['_id'] = rs_get_next_member_id(rs_config)
                    current_member = rs_get_member(rs_config, member['host'])
                    timeout = timeout - 1
                    if timeout == 0:
                        raise Exception('reached timeout while trying to add a member: %s' % e)
                    time.sleep(1)

        elif state == 'absent' and current_member:
            rs_config = rs_remove_member(rs_config, member)
            rs_reconfigure(client, rs_config)
            rs_wait_for_ok_and_primary(client)
            result['changed'] = True

    module.exit_json(**result)


from ansible.module_utils.basic import *
main()

