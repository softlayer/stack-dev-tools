import json
import sys

from glob import glob
from os import environ, remove, listdir
from os.path import exists
from time import sleep
from socket import gethostbyname
from shutil import copytree, rmtree, ignore_patterns
from multiprocessing import Value

from fabric.api import task, serial, parallel, runs_once, \
    run, local, cd, env, get, put, settings, hide
from fabric.utils import abort
from fabric.colors import red
from fabric.contrib.files import upload_template, append, sed
from fabric.decorators import with_settings

from fablib import execute_task_name, rexists
from fablib.decorators import retry
from fablib.logging import log_success, log_error, log_info, log_warn

import platforms


env.PROFILE = environ.get("SWIFT_CLUSTER_PROFILE")
env.NAME = environ.get("SWIFT_CLUSTER_NAME")
env.TEST_CONFIG = environ.get("SWIFT_TEST_CONFIG_FILE")
env.host_prefix = env.NAME
env.abort_on_prompts = True
env.running_hosts = 0
env.disable_known_hosts = True
env.use_ssh_config = True
env.user = 'root'
env.shell = '/bin/bash -c'
env.roledefs = {
    'proxy': [],
    'storage': [],
}


running_hosts = Value('H', 0)
packaged_code = Value('H', 0)
total_hosts = Value('H', 0)
tests_running = Value('H', 0)
prep_auth_done = Value('H', 0)

PlatformManager = None
platform = None


def platform_init():
    global PlatformManager
    global platform

    namespace, class_name = config('platform')
    try:
        log_info('Loading %s in %s' % (class_name, namespace))
        PlatformManager = platforms.load_manager(namespace, class_name)
    except Exception as e:
        log_error('Cannot load %s from %s: %s' % (class_name, namespace, e))
        sys.exit(1)

    helpers = {'log_success': log_success,
               'log_info': log_info,
               'log_warn': log_warn,
               'log_error': log_error,
               'execute_handler': execute_task_name,
               'run_handler': run,
               'host_broker_handler': host_broker_handler,
               'rexists': rexists}
    platform = PlatformManager(config('platform_options'), **helpers)


def host_broker_handler(vmhost):
    reimage_vm.hosts = [vmhost]
    create_datadisks.hosts = [vmhost]


def load_profile(profile_name):
    profile = json.load(open("profiles/%s" % profile_name))

    if 'platform' not in profile:
        log_error('Profile %s does not include a platform key, skipping' %
                  profile_name)
        return None

    return profile


def hosts_by_prefix(prefix):
    return ['%s%d.stack.local' % (prefix, d,) for d in xrange(1, 5)]


def index_by_host(host):
    return env.hosts.index(host)


def roledefs_from_hosts():
    env.roledefs['proxy'].append(env.hosts[0])
    env.roledefs['storage'] = env.hosts[1:]


# To load the correct profile and cluster name, we need
# these two values.
if not env.PROFILE or not env.NAME:
    print "Critical environment variables missing"
    print "\tSWIFT_CLUSTER_PROFILE=(debian-6-libvirt|" \
                                   "debian-6-softlayer-cci|"\
                                   "ubuntu-1204-libvirt|"\
                                   "freebsd-libvirt)"
    print "\tSWIFT_CLUSTER_NAME=swdev"
    sys.exit(1)


# Load the profile into our config constant and generate
# the hosts list. This modifies the env.
CONFIG = {}


for profile in listdir("profiles"):
    p = load_profile(profile)
    if not p:
        continue

    CONFIG[profile.split(".")[0]] = p

if not env.roles:
    env.roles = ['proxy', 'storage']


# Generate a list of hosts from the environment if a list
# was not provided as an argument.
if not env.hosts:
    env.hosts = hosts_by_prefix(env.host_prefix)


# Regardless of how we got here, the roledefs must be
# assigned.
roledefs_from_hosts()


def current_profile(host=None):
    profile = env.PROFILE

    # We need to be able to also pass this in for reimaging.
    host = host or env.host

    # If our profile environment setting isn't a comma-separated list,
    # return it as its value every time.
    #   eg: debian == debian,debian,debian,debian
    if not ',' in profile:
        return profile

    return profile.split(',')[env.hosts.index(host)]


def config(key, host=None):
    return CONFIG[current_profile(host)][key]


def config_package_index(name, package_set="packages"):
    for idx, pkg in enumerate(config(package_set)):
        if pkg[0] == name:
            return idx
    return None


def enable_service(service):
    if current_profile() == "freebsd":
        __enable_service_freebsd(service)
    elif current_profile() in ["ubuntu", "debian"]:
        __enable_service_ubuntu(service)


def service(service, action='start'):
    svc_cmd = config("service_manager")
    log_info("Attempting to %s %s" % (action, service))
    result = run(svc_cmd.format(service=service, action=action), pty=False)

    return result and result.succeeded


def __enable_service_ubuntu(service):
    sed("/etc/default/{service}".format(service=service.lower()),
        "{service}_ENABLE=.*".format(service=service.upper()),
        "{service}_ENABLE=true".format(service=service.upper()))


def __enable_service_freebsd(service):
    append("/etc/rc.conf", "{service}_enable=YES".format(
        service=service.lower()), escape=False)


def format_drives():
    if current_profile() == "freebsd":
        __format_drives_freebsd()
    elif current_profile() in ["ubuntu", "debian"]:
        __format_drives_ubuntu()


def __format_drives_freebsd():
    for zone, disk in enumerate(['ada'] * config("zone_count"), 1):
        device = "{1}{0}".format(zone, disk)
        run("zpool labelclear -f /dev/{device}".format(device=device))
        run("zpool create -m /srv/node/disk{zone} {device} /dev/{device}"
            .format(device=device, zone=zone))


def __format_drives_ubuntu():
    disks = run("ls -1 /dev/sd[b-z]").split()
    for zone, disk in enumerate(disks, 1):
        run("sgdisk -Z %s || true" % disk)
        run("sgdisk --clear %s" % disk)
        run("sgdisk -N 1 %s" % disk)  # default is linux data
        run("mkfs.xfs -f %s1" % disk)
        append("/etc/fstab", "%s1  /srv/node/disk%d  xfs "
               "noatime  0 2" % (disk, zone))
        run("mkdir -p /srv/node/disk%d" % zone)
        run("mount /srv/node/disk%d" % zone)


def get_address(host, private=False):
    if private:
        host = host.replace(".", ".p.", 1)
    return gethostbyname(host)


def get_short_name(host):
    return host.split('.', 1)[0]


def current_role(role):
    return env.host in env.roledefs[role]


@task
@parallel(5)
@with_settings(hide('stdout'))
def cluster_prep(name=None):
    run("hostname %s" % (env.host_string))
    run("cp -f /usr/share/zoneinfo/America/Chicago /etc/localtime")
    run("cp -f /etc/motd /etc/motd.bak")
    run("echo '' > /etc/motd")

    if rexists("/etc/rc.conf"):
        sed('/etc/rc.conf', '^hostname=.*$', 'hostname="%s"' %
            (env.host_string))
    elif rexists("/etc/hostname"):
        run("echo '' > /etc/hostname")
        append("/etc/hostname", env.host_string)

    zshrc = "config/%s/.zshrc" % name
    if exists(zshrc) and rexists("/usr/local/bin/zsh"):
        put(zshrc, "~")
        run("chsh -s /usr/local/bin/zsh")


@runs_once
def check_swift_package_deps():
    for package in config("packages"):
        pkg, branch, url = package

        if not exists("work/%s" % pkg):
            log_error("Cannot locate %s." % pkg)
            abort(red("Please run 'fab swift_deps' first."))


def swift_package_set():
    packages = set()

    for host in env.hosts:
        for package in config("packages", host):
            packages.add(tuple(package))

    return packages


@task
@runs_once
def swift_package():
    if packaged_code.value == True:  # NOQA
        return

    packaged_code.value = True

    for package in swift_package_set():
        pkg, branch, url = package

        try:
            rmtree("tmp/%s" % pkg)
        except OSError:
            print "No temp copy of %s found." % pkg

        copytree("work/%s" % pkg, "tmp/%s" % pkg,
                 ignore=ignore_patterns('.git', '.hg'))

        for patch in patches_for_package(pkg, branch):
            local("patch -d tmp/{0} -p1 < {1}".format(pkg, patch))

        local("cd tmp && tar czf {0}.tar.gz {0}".format(pkg))


def patches_for_package(package, branch):
    patches = glob("patches/%s/%s/*.patch" % (package, branch))
    patches.sort()
    return patches


@task
@runs_once
def swift_deps():
    for package in config("packages"):
        pkg, branch, url = package

        if not exists("work/%s" % pkg):
            if url.find('git') >= 0:
                local("cd work && git clone %s" % url)
                local("cd work/%s && git checkout %s" % (pkg, branch))
            elif url.find('bitbucket') >= 0:
                local("cd work && hg clone %s" % url)
                local("cd work/%s && hg checkout %s" % (pkg, branch))
            else:
                log_error("Unknown repository server for %s" % pkg)


@task
@runs_once
def swift_update_deps():
    for repo in glob("work/*"):
        local("cd %s && git pull; true" % repo)


@with_settings(hide('stdout'))
def swift_deploy_from_local(limit_packages=None):
    src_dir = "/root/src"
    run("rm -rf {0} && mkdir -p {0}".format(src_dir))

    with cd(src_dir):
        for package in config("packages"):
            pkg, branch, url = package

            if limit_packages:
                if pkg not in limit_packages:
                    log_info("Skipping: %s" % pkg)
                    continue

            with settings(warn_only=True):
                run("pip uninstall %s -y" % pkg)

            put("tmp/%s.tar.gz" % pkg, src_dir)
            run("rm -rf %s" % pkg)
            run("tar xvf %s.tar.gz" % pkg)

            with(cd(pkg)):
                run("python setup.py build")
                run("python setup.py install")


@task
@parallel(5)
def refresh_code(*args):
    check_swift_package_deps()
    swift_package()
    swift_deploy_from_local(args)
    swift_restart()


@task
@parallel(5)
def refresh_config(*args):
    check_swift_package_deps()
    upload_proxy_config()
    upload_storage_config()
    swift_restart()


@task
@parallel(5)
def rebuild_cluster():
    platform_init()
    instance = platform.find_instance(env.host)

    check_swift_package_deps()
    swift_package()

    reset_vm(instance)
    wait_for_vm()

    system_base()
    python_base()
    add_user()

    swift_deploy_from_local()
    swift_client()
    swift_config_proxy()
    swift_config_storage()
    swift_restart()

    prep_auth()

    swift_test(wait_for_prep=True)


@serial
def reimage_vm(disk, instance):
    print 'reimage_vm(%s, %s)' % (disk, instance)
    platform.reimage_instance_os(instance, disk)


@serial
def create_datadisks(disk=None, command=None):
    if not disk or not command:
        return
    if not rexists(disk):
        run(command)


def destroy_vm(instance):
    platform.reimage_instance(instance)


def reset_local():
    try:
        log_info("Removing local configs")
        remove("tmp/swift.conf")
    except:
        pass

    try:
        log_info("Removing local rings")
        for f in glob("tmp/*.ring.gz"):
            remove(f)
    except:
        pass


def reset_vm(instance):
    log_warn("Killing %s" % instance.name)
    destroy_vm(instance)


def wait_for_vm():
    online = False
    with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                  warn_only=True):
        while not online:
            try:
                run("echo")
                log_success("VM is up!")
                online = True
            except:
                log_info("Waiting for VM to boot...")
                sleep(4)


def system_base():
    packages = config('system_packages')
    with settings(hide('warnings', 'running', 'stdout', 'stderr')):
        if current_profile() in ["ubuntu", "debian"]:
            run("apt-get update")

        for package in packages:
            log_info("Installing: {0}".format(package))
            run("{mgr} {pkg}; true".format(
                mgr=config("package_manager"),
                pkg=package))


@task
def python_base():
    packages = config("python_packages")
    with settings(hide('warnings', 'running', 'stdout', 'stderr')):
        for package in packages:
            log_info("Installing: {0}".format(package))
            run("pip install {0}; true".format(package))


def add_user():
    prefix = ''
    if current_profile() == "freebsd":
        prefix = '/usr/local'
        run("echo 'swift::::::Swift::/bin/sh:' | adduser -f - -w no")
    else:
        run("adduser --system --no-create-home --shell /bin/sh "
            "--disabled-password --group swift")
    run("echo 'swift ALL=(ALL) ALL' >> {prefix}/etc/sudoers".format(
        prefix=prefix))
    run("test -d /etc/swift || mkdir -p /etc/swift")
    run("chown -R swift:swift /etc/swift")


@task
@runs_once
def swift_create_user(auth_key, account, username, password):
    if not current_role('proxy'):
        return
    run("swauth-add-user -A http://{0}/auth/"
        " -K {1} -a {2} {3} {4}".format(env.host, auth_key, account, username,
                                        password))


@runs_once
def initialize_swauth(auth_key):
    run("swauth-initialize -A http://{0}/auth/"
        " -K {1}".format(env.host, auth_key))


def init_logfiles():
    logfiles = ["all.log", "swift/proxy.log", "swift/proxy.error",
                "swift/log_processor.log"]

    # Create log directories all the way down to
    # the hourly folder.
    run("test -d /var/log/swift/hourly || mkdir -p /var/log/swift/hourly")

    # Finally touch and chmod all the log files
    for logfile in logfiles:
        run("touch /var/log/%s" % logfile)
        run("chmod 600 /var/log/%s" % logfile)


def swift_config_proxy():
    if not current_role('proxy'):
        return

    reset_local()
    init_logfiles()

    upload_proxy_config()

    for svc in config("proxy_services"):
        enable_service(svc)
        service(svc, action='restart')

    run("openssl req -new -x509 -nodes -batch -out /etc/swift/cert.crt "
        "-keyout /etc/swift/cert.key")

    with cd("/etc/swift"):
        run("swift-ring-builder account.builder create 12 2 1")
        run("swift-ring-builder container.builder create 12 2 1")
        run("swift-ring-builder object.builder create 12 2 1")

        for node in env.roledefs['storage']:
            for zone in xrange(1, config("zone_count"), 1):
                conf = (zone, get_address(node, private=True), zone, 100)

                run("swift-ring-builder account.builder "
                    "add z{0}-{1}:6002/disk{2} {3}".format(*conf))
                run("swift-ring-builder container.builder "
                    "add z{0}-{1}:6001/disk{2} {3}".format(*conf))
                run("swift-ring-builder object.builder "
                    "add z{0}-{1}:6000/disk{2} {3}".format(*conf))

        run("swift-ring-builder account.builder rebalance")
        run("swift-ring-builder container.builder rebalance")
        run("swift-ring-builder object.builder rebalance")

    get("/etc/swift/swift.conf", "tmp")
    get("/etc/swift/*.ring.gz", "tmp")
    log_success("Downloaded config and rings from proxy")


def swift_config_storage():
    if not current_role('storage'):
        return

    init_logfiles()
    format_drives()

    run("mkdir -p /etc/swift/{object,container,account}")
    run("mkdir -p /var/cache/swift")
    run("mkdir -p /var/lock")
    run("chmod o+x /var/cache")
    run("chown -R swift:swift /srv/node /etc/swift /var/cache/swift")

    # try and make /proc available in BSD
    if current_profile() == 'freebsd':
        run("mkdir -p /compat/linux/proc")
        run("rmdir /proc ; ln -s /compat/linux/proc /proc")
        append("/etc/fstab", "linprocfs /compat/linux/proc linprocfs rw 0 0",
               escape=False)
        append("/boot/loader.conf.local", "linprocfs_load=YES", escape=False)

    upload_storage_config()

    for svc in config("storage_services"):
        enable_service(svc)
        service(svc, action='restart')

    # While the ring files and conf don't exist, wait.
    while (not exists("tmp/swift.conf")
            or not exists("tmp/account.ring.gz")
            or not exists("tmp/container.ring.gz")
            or not exists("tmp/object.ring.gz")):
        log_info("Waiting for local config and ring files...")
        sleep(5)

    put("tmp/swift.conf", "/etc/swift")
    put("tmp/*.ring.gz", "/etc/swift")


def upload_storage_config():
    if not current_role('storage'):
        return
    address = get_address(env.host, private=True)
    host = get_short_name(env.host)
    upload_template("config/account-server.conf",
                    "/etc/swift/account-server.conf",
                    {'private_address': address,
                     'host_short': host}, backup=False)
    upload_template("config/container-server.conf",
                    "/etc/swift/container-server.conf",
                    {'private_address': address,
                     'host_short': host}, backup=False)
    upload_template("config/object-server.conf",
                    "/etc/swift/object-server.conf",
                    {'private_address': address,
                     'host_short': host}, backup=False)
    prefix = ''
    if current_profile() == 'freebsd':
        prefix = '/usr/local'
    upload_template("config/rsyncd.conf", "{prefix}/etc/rsyncd.conf".format(
        prefix=prefix), {'private_address': address}, backup=False)
    upload_template("config/rsyslog.conf", "{prefix}/etc/rsyslog.conf".format(
        prefix=prefix), backup=False)


def upload_proxy_config():
    if not current_role('proxy'):
        return
    address = get_address(env.host, private=True)

    # Upload proxy configs

    if not rexists("/etc/swift/swift.conf"):
        # Generate a secure secret server-side
        log_info("Not swift.conf found, generating ring!")
        hash_prefix = local("od -t x4 -N 8 -A n </dev/random"
                            "| sed -e 's/ //g'", capture=True)
    else:
        hash_prefix = run("grep swift_hash_path_suffix /etc/swift/swift.conf "
                          "| sed -e 's/.*=[[:space:]]*//'")

    swift_sync_key = hash_prefix
    super_admin_key = hash_prefix

    upload_template("config/dispersion.conf", "/etc/swift/dispersion.conf",
                    {'private_address': address}, backup=False)
    upload_template("config/proxy-server.conf", "/etc/swift/proxy-server.conf",
                    {'private_address': address,
                     'host': env.host,
                     'swift_sync_key': swift_sync_key,
                     'super_admin_key': super_admin_key,
                     'host_prefix': env.host_prefix,
                     'host_short': get_short_name(env.host)}, backup=False)
    upload_template("config/swift.conf", "/etc/swift/swift.conf",
                    {'hash_prefix': hash_prefix}, backup=False)
    prefix = ''
    if current_profile() == 'freebsd':
        prefix = '/usr/local'
    upload_template("config/rsyslog.conf", "{prefix}/etc/rsyslog.conf".format(
        prefix=prefix), backup=False)


@task
@runs_once
def swift_test(wait_for_prep=False):
    if tests_running.value == True:  # NOQA
        return

    tests_running.value = True

    if wait_for_prep:
        while not all([running_hosts.value >= len(env.hosts),
                       prep_auth_done.value == True]): # NOQA
            log_info("Waiting for all hosts to be available "
                     "before testing...")
            sleep(1)

    if not env.TEST_CONFIG:
        abort(red("Please set your SWIFT_TEST_CONFIG_FILE environment "
                  "variable to a valid config file location."))

    log_success("Running functional test suite...")
    local("cd tmp/swift && ./.functests")


@task
@parallel(5)
def swift_restart():
    global running_hosts

    while not rexists("/etc/swift/swift.conf"):
        log_info("Still need swift.conf...")
        sleep(2)
    while not rexists("/etc/swift/account.ring.gz"):
        log_info("Still need account.ring.gz...")
        sleep(2)
    while not rexists("/etc/swift/container.ring.gz"):
        log_info("Still need container.ring.gz...")
        sleep(2)
    while not rexists("/etc/swift/object.ring.gz"):
        log_info("Still need object.ring.gz...")
        sleep(2)

    run("swift-init stop all; true")
    run("swift-init start all; true")

    log_success("Restarted!")
    running_hosts.value += 1

    while running_hosts.value < len(env.hosts):
        log_info("%d/%d hosts running" % (running_hosts.value, len(env.hosts)))
        sleep(1)


@task
@retry(5)
def prep_auth():
    if not current_role('proxy'):
        return

    hash_prefix = run("grep super_admin_key /etc/swift/proxy-server.conf "
                      "| sed -e 's/.*=[[:space:]]*//'")
    run("swauth-prep -A http://{0}/auth/ -K {1}".format(env.host, hash_prefix))

    users = []
    users.append(('test', 'tester', 'testing'))
    users.append(('test2', 'tester2', 'testing2'))
    users.append(('test', 'tester3', 'testing3'))

    accounts = []
    accounts.append('myaccount')

    for user in users:
        run("swauth-add-user -A http://{0}/auth/ -K {1} "
            "-a {2} {3} {4}".format(env.host, hash_prefix, *user))

    for account in accounts:
        run("swauth-add-account -A http://{0}/auth/ -K {1} "
            "{2}".format(env.host, hash_prefix, account))

    prep_auth_done.value = True


@task
def swift_client():
    src_dir = "/root/src"

    with cd(src_dir):
        run("git clone git://github.com/openstack/python-swiftclient.git "
            "swift-client")

        with cd("swift-client"):
            with settings(warn_only=True):
                run("pip uninstall swiftclient -y")

            run("python setup.py build")
            run("python setup.py install")
