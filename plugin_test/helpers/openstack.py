"""Copyright 2016 Mirantis, Inc.

Licensed under the Apache License, Version 2.0 (the "License"); you may
not use this file except in compliance with the License. You may obtain
copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
License for the specific language governing permissions and limitations
under the License.
"""
import time

import paramiko
import yaml
from devops.helpers.helpers import icmp_ping
from devops.helpers.helpers import tcp_ping
from devops.helpers.helpers import wait

from fuelweb_test import logger
from fuelweb_test.helpers.ssh_manager import SSHManager
from fuelweb_test.helpers.utils import pretty_log

# timeouts
BOOT_TIMEOUT = 300


def get_defaults():
    """Get default parameters from config.yaml."""
    with open('plugin_test/helpers/config.yaml') as config:
        defaults = yaml.load(config.read())
        logger.info(''.format(defaults))
        return defaults

# defaults
external_net_name = get_defaults()['networks']['floating']['name']
zone_image_maps = get_defaults()['zone_image_maps']
instance_creds = (
    get_defaults()['os_credentials']['cirros']['user'],
    get_defaults()['os_credentials']['cirros']['password'])


def verify_instance_state(os_conn, instances=None, expected_state='ACTIVE',
                          boot_timeout=BOOT_TIMEOUT):
    """Verify that current state of each instance/s is expected.

    :param os_conn: type object, openstack
    :param instances: type list, list of created instances
    :param expected_state: type string, expected state of instance
    :param boot_timeout: type int, time in seconds to build instance
    """
    if not instances:
        instances = os_conn.nova.servers.list()
    for instance in instances:
        wait(
            lambda:
            os_conn.get_instance_detail(instance).status == expected_state,
            timeout=BOOT_TIMEOUT,
            timeout_msg="Timeout is reached. "
                        "Current state of Vm {0} is {1}."
                        "Expected state is {2}".format(
                            instance.name,
                            os_conn.get_instance_detail(instance).status,
                            expected_state)
        )


def create_instances(os_conn, nics, vm_count=1,
                     security_groups=None, available_hosts=None):
    """Create Vms on available hypervisors.

    :param os_conn: type object, openstack
    :param vm_count: type integer, count of VMs to create
    :param nics: type dictionary, neutron networks
                     to assign to instance
    :param security_groups: A list of security group names
    :param available_hosts: available hosts for creating instances
    """
    # Get list of available images,flavors and hipervisors
    instances = []
    images_list = os_conn.nova.images.list()
    flavors = os_conn.nova.flavors.list()
    flavor = [f for f in flavors if f.name == 'm1.micro'][0]
    if not available_hosts:
        available_hosts = os_conn.nova.services.list(binary='nova-compute')
    for host in available_hosts:
        image = [image for image in images_list
                 if image.name == zone_image_maps[host.zone]][0]
        instance = os_conn.nova.servers.create(
            flavor=flavor,
            name='test_{0}'.format(image.name),
            image=image, min_count=vm_count,
            availability_zone='{0}:{1}'.format(host.zone, host.host),
            nics=nics, security_groups=security_groups
        )
        instances.append(instance)
    return instances


def check_connection_vms(ip_pair, command='pingv4', result_of_command=0,
                         timeout=30, interval=5):
    """Check network connectivity between instances.

    :param ip_pair: type dict, {ip_from: [ip_to1, ip_to2, etc.]}
    :param command: type string, key from dictionary 'commands'
                    by default is 'pingv4'
    :param result_of_command: type integer, exit code of command execution
                              by default is 0
    :param timeout: wait to get expected result
    :param interval: interval of executing command
    """
    commands = {
        "pingv4": "ping -c 5 {}",
        "pingv6": "ping6 -c 5 {}",
        "arping": "sudo arping -I eth0 {}",
        "ssh": " "}

    msg = 'Command "{0}", Actual exit code is NOT {1}'
    for ip_from in ip_pair:
        with get_ssh_connection(ip_from, instance_creds[0],
                                instance_creds[1]) as ssh:
            for ip_to in ip_pair[ip_from]:
                logger.info("Check connection from {0} to {1}.".format(
                    ip_from, ip_to))
                cmd = commands[command].format(ip_to)

                wait(lambda:
                     execute(ssh, cmd)['exit_code'] == result_of_command,
                     interval=interval,
                     timeout=timeout,
                     timeout_msg=msg.format(cmd, result_of_command)
                     )


def check_connection_through_host(remote, ip_pair, command='pingv4',
                                  result_of_command=0, timeout=30,
                                  interval=5):
    """Check network connectivity between instances.

    :param ip_pair: type list,  ips of instances
    :param remote: Access point IP
    :param command: type string, key from dictionary 'commands'
                    by default is 'pingv4'
    :param  result_of_command: type integer, exit code of command execution
                               by default is 0
    :param timeout: wait to get expected result
    :param interval: interval of executing command
    """
    commands = {
        "pingv4": "ping -c 5 {}",
        "pingv6": "ping6 -c 5 {}",
        "arping": "sudo arping -I eth0 {}"}

    msg = 'Command "{0}", Actual exit code is NOT {1}'

    for ip_from in ip_pair:
        for ip_to in ip_pair[ip_from]:
            logger.info('Check ping from {0} to {1}'.format(ip_from, ip_to))
            cmd = commands[command].format(ip_to)
            wait(
                lambda:
                remote_execute_command(
                    remote,
                    ip_from,
                    cmd,
                    wait=timeout)['exit_code'] == result_of_command,
                interval=interval,
                timeout=timeout,
                timeout_msg=msg.format(cmd, result_of_command)
            )


def ping_each_other(ips, command='pingv4', expected_ec=0,
                    timeout=30, interval=5, access_point_ip=None):
    """Check network connectivity between instances.

    :param ips: list, list of ips
    :param command: type string, key from dictionary 'commands'
                    by default is 'pingv4'
    :param expected_ec: type integer, exit code of command execution
                              by default is 0
    :param timeout: wait to get expected result
    :param interval: interval of executing command
    :param access_point_ip: It is used if check via host
    """
    ip_pair = {key: [ip for ip in ips if ip != key] for key in ips}
    if access_point_ip:
        check_connection_through_host(remote=access_point_ip,
                                      ip_pair=ip_pair,
                                      command=command,
                                      result_of_command=expected_ec,
                                      timeout=timeout,
                                      interval=interval)
    else:
        check_connection_vms(ip_pair=ip_pair,
                             command=command,
                             result_of_command=expected_ec,
                             timeout=timeout,
                             interval=interval)


def create_and_assign_floating_ips(os_conn, instances_list):
    """Associate floating ips with specified instances.

    :param os_conn: type object, openstack
    :param instances_list: type list, instances
    """
    fips = []
    for instance in instances_list:
            ip = os_conn.assign_floating_ip(instance).ip
            fips.append(ip)
            wait(lambda: icmp_ping(ip), timeout=60 * 5, interval=5)
    return fips


def get_ssh_connection(ip, username, userpassword, timeout=30, port=22):
    """Get ssh to host.

    :param ip: string, host ip to connect to
    :param username: string, a username to use for authentication
    :param userpassword: string, a password to use for authentication
    :param timeout: timeout (in seconds) for the TCP connection
    :param port: host port to connect to
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        ip, port=port, username=username,
        password=userpassword, timeout=timeout
    )
    return ssh


def execute(ssh_client, command):
    """Execute command on remote host.

    :param ssh_client: SSHClient to instance
    :param command: type string, command to execute
    """
    channel = ssh_client.get_transport().open_session()
    channel.exec_command(command)
    result = {
        'stdout': [],
        'stderr': [],
        'exit_code': 0
    }
    result['exit_code'] = channel.recv_exit_status()
    result['stdout'] = channel.recv(1024)
    result['stderr'] = channel.recv_stderr(1024)
    return result


def remote_execute_command(instance1_ip, instance2_ip, command, wait=30):
    """Check execute remote command.

    :param instance1_ip: string, instance ip connect from
    :param instance2_ip: string, instance ip connect to
    :param command: string, remote command
    :param wait: integer, time to wait available ip of instances
    """
    with get_ssh_connection(
        instance1_ip, instance_creds[0], instance_creds[1]
    ) as ssh:

        interm_transp = ssh.get_transport()
        try:
            logger.info("Opening channel between VMs {0} and {1}".format(
                instance1_ip, instance2_ip))
            interm_chan = interm_transp.open_channel('direct-tcpip',
                                                     (instance2_ip, 22),
                                                     (instance1_ip, 0))
        except Exception as e:
            message = "{} Wait to update sg rules. Try to open channel again"
            logger.info(message.format(e))
            time.sleep(wait)
            interm_chan = interm_transp.open_channel('direct-tcpip',
                                                     (instance2_ip, 22),
                                                     (instance1_ip, 0))
        transport = paramiko.Transport(interm_chan)
        transport.start_client()
        logger.info("Passing authentication to VM")
        transport.auth_password(
            instance_creds[0], instance_creds[1])
        channel = transport.open_session()
        channel.get_pty()
        channel.fileno()
        channel.exec_command(command)

        result = {
            'stdout': [],
            'stderr': [],
            'exit_code': 0
        }
        logger.debug("Receiving exit_code, stdout, stderr")
        result['exit_code'] = channel.recv_exit_status()
        result['stdout'] = channel.recv(1024)
        result['stderr'] = channel.recv_stderr(1024)
        logger.debug('Command: {}'.format(command))
        logger.debug(pretty_log(result))
        logger.debug("Closing channel")
        channel.close()

        return result


def get_role(os_conn, role_name):
    """Get role by name."""
    role_list = os_conn.keystone.roles.list()
    for role in role_list:
        if role.name == role_name:
            return role
    return None


def add_role_to_user(os_conn, user_name, role_name, tenant_name):
    """Assign role to user.

    :param os_conn: type object
    :param user_name: type string,
    :param role_name: type string
    :param tenant_name: type string
    """
    tenant_id = os_conn.get_tenant(tenant_name).id
    user_id = os_conn.get_user(user_name).id
    role_id = get_role(os_conn, role_name).id
    os_conn.keystone.roles.add_user_role(user_id, role_id, tenant_id)


def check_service(ip, commands):
        """Check that required nova services are running on controller.

        :param ip: ip address of node
        :param commands: type list, nova commands to execute on controller,
                         example of commands:
                         ['nova-manage service list | grep vcenter-vmcluster1'
        """
        ssh_manager = SSHManager()
        ssh_manager.execute_on_remote(ip=ip, cmd='source openrc')

        for cmd in commands:
            wait(
                lambda:
                ':-)' in ssh_manager.execute_on_remote(ip=ip,
                                                      cmd=cmd)['stdout'][-1],
                timeout=200)


def create_volume(os_conn, availability_zone, size=1,
                  expected_state="available"):
    """Create volume.

    :param os_conn: type object, openstack
    :param availability_zone: type string,
     availability_zone where volume will be created
    :param expected_state: type string, expected state of instance
    :param size: type int, size of volume
    """
    images_list = os_conn.nova.images.list()
    image = [
        image for image
        in images_list
        if image.name == zone_image_maps[availability_zone]][0]
    volume = os_conn.cinder.volumes.create(
        size=size, imageRef=image.id, availability_zone=availability_zone)
    wait(
        lambda: os_conn.cinder.volumes.get(volume.id).status == expected_state,
        timeout=BOOT_TIMEOUT)
    logger.info("Created volume: '{0}', parent image: '{1}'"
                .format(volume.id, image.id))
    return volume


def create_access_point(os_conn, nics, security_groups):
        """Create access point.

        Creating instance with floating ip as access point to instances
        with private ip in the same network.

        :param os_conn: type object, openstack
        :param nics: type dictionary, neutron networks
                     to assign to instance
        :param security_groups: A list of security group names
        """
        # get any available host
        host = os_conn.nova.services.list(binary='nova-compute')[0]
        # create access point server
        access_point = create_instances(
            os_conn=os_conn, nics=nics,
            vm_count=1,
            security_groups=security_groups,
            available_hosts=[host]).pop()

        verify_instance_state(os_conn)

        access_point_ip = os_conn.assign_floating_ip(
            access_point, use_neutron=True)['floating_ip_address']
        wait(lambda: tcp_ping(access_point_ip, 22), timeout=60 * 5, interval=5)
        return access_point, access_point_ip
