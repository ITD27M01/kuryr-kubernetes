# Copyright (c) 2016 Mirantis, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from functools import partial
import ipaddress
import os

from debtcollector import removals
from kuryr.lib import utils
from openstack import connection
from openstack import exceptions as os_exc
from openstack.network.v2 import port as os_port
from openstack.network.v2 import trunk as os_trunk
from openstack import utils as os_utils

from kuryr_kubernetes import config
from kuryr_kubernetes import k8s_client
from kuryr_kubernetes.pod_resources import client as pr_client

_clients = {}
_NEUTRON_CLIENT = 'neutron-client'
_KUBERNETES_CLIENT = 'kubernetes-client'
_OPENSTACKSDK = 'openstacksdk'
_POD_RESOURCES_CLIENT = 'pod-resources-client'


def get_network_client():
    return _clients[_OPENSTACKSDK].network


@removals.remove
def get_neutron_client():
    return _clients[_NEUTRON_CLIENT]


def get_openstacksdk():
    return _clients[_OPENSTACKSDK]


def get_loadbalancer_client():
    return get_openstacksdk().load_balancer


def get_kubernetes_client():
    return _clients[_KUBERNETES_CLIENT]


def get_pod_resources_client():
    return _clients[_POD_RESOURCES_CLIENT]


def get_compute_client():
    return _clients[_OPENSTACKSDK].compute


def setup_clients():
    setup_neutron_client()
    setup_kubernetes_client()
    setup_openstacksdk()


def setup_neutron_client():
    _clients[_NEUTRON_CLIENT] = utils.get_neutron_client()


def setup_kubernetes_client():
    if config.CONF.kubernetes.api_root:
        api_root = config.CONF.kubernetes.api_root
    else:
        # NOTE(dulek): This is for containerized deployments, i.e. running in
        #              K8s Pods.
        host = os.environ['KUBERNETES_SERVICE_HOST']
        port = os.environ['KUBERNETES_SERVICE_PORT_HTTPS']
        try:
            addr = ipaddress.ip_address(host)
            if addr.version == 6:
                host = '[%s]' % host
        except ValueError:
            # It's not an IP addres but a hostname, it's fine, move along.
            pass
        api_root = "https://%s:%s" % (host, port)
    _clients[_KUBERNETES_CLIENT] = k8s_client.K8sClient(api_root)


def _create_ports(self, payload):
    """bulk create ports using openstacksdk module"""
    # TODO(gryf): this should be implemented on openstacksdk instead.
    response = self.post(os_port.Port.base_path, json=payload)

    if not response.ok:
        raise os_exc.SDKException('Error when bulk creating ports: %s',
                                  response.text)
    return (os_port.Port(**item) for item in response.json()['ports'])


def _add_trunk_subports(self, trunk, subports):
    """Set sub_ports on trunk

    The original method on openstacksdk doesn't care about any errors. This is
    a replacement that does.
    """
    trunk = self._get_resource(os_trunk.Trunk, trunk)
    url = os_utils.urljoin('/trunks', trunk.id, 'add_subports')
    response = self.put(url, json={'sub_ports': subports})
    os_exc.raise_from_response(response)
    trunk._body.attributes.update({'sub_ports': subports})
    return trunk


def _delete_trunk_subports(self, trunk, subports):
    """Remove sub_ports from trunk

    The original method on openstacksdk doesn't care about any errors. This is
    a replacement that does.
    """
    trunk = self._get_resource(os_trunk.Trunk, trunk)
    url = os_utils.urljoin('/trunks', trunk.id, 'remove_subports')
    response = self.put(url, json={'sub_ports': subports})
    os_exc.raise_from_response(response)
    trunk._body.attributes.update({'sub_ports': subports})
    return trunk


def handle_neutron_errors(method, *args, **kwargs):
    """Handle errors on openstacksdk router methods"""
    result = method(*args, **kwargs)
    if 'NeutronError' in result:
        error = result['NeutronError']
        if error['type'] in ('RouterNotFound',
                             'RouterInterfaceNotFoundForSubnet',
                             'SubnetNotFound'):
            raise os_exc.NotFoundException(message=error['message'])
        else:
            raise os_exc.SDKException(error['type'] + ": " + error['message'])

    return result


def setup_openstacksdk():
    auth_plugin = utils.get_auth_plugin('neutron')
    session = utils.get_keystone_session('neutron', auth_plugin)
    conn = connection.Connection(
        session=session,
        region_name=getattr(config.CONF.neutron, 'region_name', None))
    conn.network.create_ports = partial(_create_ports, conn.network)
    conn.network.add_trunk_subports = partial(_add_trunk_subports,
                                              conn.network)
    conn.network.delete_trunk_subports = partial(_delete_trunk_subports,
                                                 conn.network)
    _clients[_OPENSTACKSDK] = conn


def setup_pod_resources_client():
    root_dir = config.CONF.sriov.kubelet_root_dir
    _clients[_POD_RESOURCES_CLIENT] = pr_client.PodResourcesClient(root_dir)
