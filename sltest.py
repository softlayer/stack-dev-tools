#!/usr/bin/env python
from sys import stdout
from os import getenv
from time import time, sleep
from SoftLayer import Client
from SoftLayer.CCI import CCIManager


cluster = 'swift-dev'
host_format = '%s%d.stack.local'
host_count = 4
hosts = [host_format % (cluster, x) for x in range(1, host_count + 1)]

# Uses SoftLayer CCI Manager to provision 4 swift dev machines
client = Client(username=getenv('SL_USERNAME'), api_key=getenv('SL_API_KEY'))
ccis = CCIManager(client)
instances = []


def is_proxy(host):
    proxy_prefix = '%s1' % cluster
    return host[0:len(proxy_prefix)] == proxy_prefix


def get_cci(host):
    global instances
    if not instances:
        instances = ccis.list_instances()

    instance = None
    for ii in instances:
        if ii.get('fullyQualifiedDomainName', '') == host:
            instance = ii
            print 'found %s as cci %d' % (host, ii.get('id'))
            break

    return instance


def create_cci(host, proxy=False):
    hostname = host.split('.', 1)[0]
    domain = host.split('.', 1)[1]
    type = 'proxy' if proxy else 'storage'
    print 'creating cci %s node %s/%s' % (type, hostname, domain)

    base_options = {'cpus': 2,
                    'memory': 2048,
                    'hostname': hostname,
                    'domain': domain,
                    'datacenter': 'sjc01',
                    'os_code': 'DEBIAN_6_64'}
    print base_options
    return ccis.create_instance(**base_options)


def get_cci_passwords(instance):
    passwords = [(x['username'], x['password']) for x in
                 ccis.get_instance_passwords(instance['id'])]
    return passwords


def cancel_cci(instance=None):
    print 'canceling cci %s (%d)' % (instance['fullyQualifiedDomainName'],
                                     instance['id'])
    return ccis.cancel_instance(instance['id'])


def reimage_cci(instance=None):
    print 'reimaging cci %s (%d)' % (instance['fullyQualifiedDomainName'],
                                     instance['id'])
    return ccis.reload_instance(instance['id'])


def cci_ready(instance):
    return 'activeTransaction' not in instance


def wait_for_reload_start(instance):
    wait_start = time()
    stdout.write('Waiting for reload to start on %s...' %
                 instance['fullyQualifiedDomainName'])
    stdout.flush()

    while cci_ready(instance):
        sleep(1)
        instance = ccis.get_instance(instance['id'])
        stdout.write('.')
        stdout.flush()

    print 'began after %0.3f secs.' % (time() - wait_start)
    return instance


def wait_for_cci(instance):
    wait_start = time()
    stdout.write('Waiting for %s...' %
                 instance['fullyQualifiedDomainName'])
    stdout.flush()

    while not cci_ready(instance):
        sleep(3)
        instance = ccis.get_instance(instance['id'])
        stdout.write('.')
        stdout.flush()

    print 'available after %0.3f secs.' % (time() - wait_start)
    return instance


def destroy_vm(host):
    print 'destroying %s' % host
    instance = get_cci(host)
    if not instance:
        print 'could not find host %s!' % host
        return False

    reimage_result = reimage_cci(instance)
    print 'result: %s' % reimage_result


if __name__ == '__main__':
    for host in hosts:
        cci = get_cci(host)
        id = cci['id'] if cci else None

        print 'Host: %s (instance %s)' % (host, id)

        if not id:
            res = create_cci(host, proxy=is_proxy(host))
            cci = wait_for_cci(cci)
        else:
            reimage_cci(cci)
            cci = wait_for_reload_start(cci)
            cci = wait_for_cci(cci)

            passwords = get_cci_passwords(cci)
            (_, root_pass) = passwords[0] if passwords else (None, None)
            print '%s password: root/%s' % (host, root_pass)
