from os import getenv
from time import time, sleep
from core import Platform, Instance
from SoftLayer import Client
from SoftLayer.CCI import CCIManager
from paramiko import SSHClient


class _SuppressPolicy(object):
    def missing_host_key(self, client, hostname, key):
        pass


class CCIPlatform(Platform):
    _required_opts = ['cores', 'memory', 'domain',
                      'datacenter', 'os_code']

    def _on_init(self):
        self._client = Client(username=getenv('SL_USERNAME'),
                              api_key=getenv('SL_API_KEY'))
        self._manager = CCIManager(self._client)

    def find_instance(self, host_name):
        instance = None
        host_name = host_name.lower()

        for ii in self._manager.list_instances():
            fqdn = ii.get('fullyQualifiedDomainName', '')
            if fqdn.lower() == host_name:
                instance = Instance(id=ii.get('id'), name=fqdn)
                break

        return instance

    def get_instance(self, id):
        cci = self._manager.get_instance(id)
        return self._cci_to_instance(cci)

    def create_instance(self, host_name):
        host_bits = host_name.split('.', 1)
        host_name = host_bits[0]
        domain = host_bits[1] if len(host_bits) >= 2 else self.config('domain')

        base_options = {'cpus': self.config('cores'),
                        'memory': self.config('memory'),
                        'hostname': host_name,
                        'domain': domain,
                        'datacenter': self.config('datacenter'),
                        'os_code': self.config('os_code')}

        print 'creating cci %s/%s' % (host_name, domain)
        print base_options
        cci = self._manager.create_instance(**base_options)
        cci = self._cci_await_ready(cci)

        self._cci_install_keys(cci['id'])

        return self._cci_to_instance(cci)

    def reimage_instance(self, instance):
        self._manager.reload_instance(instance.id)
        cci = self._manager.get_instance(instance.id)
        cci = self._cci_await_transaction_start(cci)
        cci = self._cci_await_ready(cci)

        self._cci_install_keys(cci['id'])

        return self._cci_to_instance(cci)

    def delete_instance(self, instance):
        self._manager.cancel_instance(instance.id)
        self._cci_await_delete(self._manager.get_instance(instance.id))

    def instance_ready(self, instance):
        cci = self._manager.get_instance(instance.id)
        return (cci and 'activeTransaction' not in cci)

    def _cci_to_instance(self, cci):
        if not cci:
            return None
        return Instance(id=cci['id'], name=cci['fullyQualifiedDomainName'])

    def _cci_await_state(self, cci, state_check, sleep_secs=5):
        wait_start = time()
        self.log_info('Waiting for %s to change state...' % (cci['id']))

        while state_check(cci):
            sleep(sleep_secs)
            cci = self._manager.get_instance(cci['id'])
            self.log_info('...')

        self.log_info('Available after %0.3f secs.' % (time() - wait_start))
        return cci

    def _cci_await_ready(self, cci):
        return self._cci_await_state(cci,
                                     lambda c: 'activeTransaction' in c,
                                     sleep_secs=5)

    def _cci_await_transaction_start(self, cci):
        return self._cci_await_state(cci,
                                     lambda c: 'activeTransaction' not in c,
                                     sleep_secs=2)

    def _cci_await_delete(self, cci):
        return self._cci_await_state(cci,
                                     lambda c: c and 'id' in c,
                                     sleep_secs=2)

    def _get_cci_root_password(self, cci):
        passwords = self._manager.get_instance_passwords(cci['id'])
        password = None

        for p in passwords:
            if 'username' in p and p['username'] == 'root':
                password = p['password']
                break

        return password

    def _cci_install_keys(self, id):
        cci = self._manager.get_instance(id)
        password = self._get_cci_root_password(cci)

        if not password:
            raise Exception('Passwords are not available for instance %s' %
                            cci['id'])

        keys_url = self.config('ssh_key_url')
        if not keys_url:
            return

        client_settings = {'hostname': cci['primaryIpAddress'],
                           'username': 'root',
                           'password': password}
        client = SSHClient()
        client.set_missing_host_key_policy(_SuppressPolicy())
        client.connect(look_for_keys=False, **client_settings)

        client.exec_command('mkdir -p ~/.ssh')
        client.exec_command('wget -T 10 -q -O ~/.ssh/authorized_keys %s' %
                            keys_url)
        client.close()
