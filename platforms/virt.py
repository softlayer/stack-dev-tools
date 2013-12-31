from core import Platform, Instance
import libvirt
from lxml import etree
from StringIO import StringIO
from os.path import basename
from time import sleep


class LibVirtPlatform(Platform):
    _required_opts = ['vmhost_count', 'vmhost_pattern',
                      'base_image_url', 'base_image']

    def _on_init(self):
        self._conn = None
        self._vmhosts = [self.config('vmhost_pattern') % x for x in
                         range(1, int(self.config('vmhost_count')) + 1)]

    def find_instance(self, host_name):
        instance = None
        vm_host = None
        domain = None
        disk_names = None
        short_name = host_name.split('.')[0]

        for vm_host in self._vmhosts:
            self.log_info("Looking for %s on %s" % (short_name, vm_host))
            conn = libvirt.open('qemu+ssh://root@%s/system' % vm_host)
            doms = []

            for dm in conn.listDomainsID():
                try:
                    doms.append(conn.lookupByID(dm))
                except libvirt.libvirtError:
                    pass
            doms.extend(
                [conn.lookupByName(dm) for dm in conn.listDefinedDomains()])

            for dom in doms:
                domxml = None
                while not domxml:
                    try:
                        domxml = etree.parse(
                            StringIO(
                                dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE |
                                            libvirt.VIR_DOMAIN_XML_SECURE)))
                    except libvirt.libvirtError:
                        self.log_error('Whoops, race issues on libvirt, '
                                       'retrying')
                        sleep(1)

                vm_name = domxml.xpath('//name/text()')[0]

                if short_name.lower() != vm_name.lower():
                    continue

                self.log_success("Found %s(%s) on %s" % (vm_name, short_name,
                                                         vm_host))
                disk_names = domxml.xpath("//disk[@type='block']/source/@dev")
                domain = dom
                break

            conn.close()

            if domain:
                instance = Instance(id=short_name, name=host_name)
                instance.vm_host = vm_host
                instance.domain = domain
                instance.disk_names = disk_names
                break

        return instance

    def create_instance(self, host_name):
        raise Exception("Cannot create a new instance via libvirt.")

    def reimage_instance(self, instance):
        self.host_broker_handler(instance.vm_host)

        try:
            instance.domain.destroy()
            self.log_success("Destroyed VM")
            sleep(1)
        except libvirt.libvirtError:
            pass

        for disk in instance.disk_names[:1]:
            self.log_success("Reimaging %s %s" % (disk, instance.id))
            self.execute_handler('reimage_vm', disk, instance)

        if len(instance.disk_names) > 1:
            for disk in instance.disk_names[1:]:
                self.log_success("Creating data-disk %s" % disk)
                command = "lvcreate -L100G -n %s sheepdog" % basename(disk)
                self.execute_handler('create_datadisks', disk, command)

        instance.domain.create()
        self.log_success("Powered on VM")

    def reimage_instance_os(self, instance, disk):
        base_img = self.config('base_image')
        img_url = '%(url)s%(base)s.raw.xz' % {
            'base': base_img,
            'url': self.config('base_image_url')}

        if self.rexists(disk):
            self.log_info("Deleting %s" % disk)
            self.run_handler("lvremove -f %s" % disk)

        if not self.rexists('/dev/sheepdog/%s' % base_img):
            self.log_info("Downloading %s base template" % base_img)
            self.run_handler("lvcreate -L4G -n %s sheepdog" % base_img)
            self.run_handler("wget -q -O - %(url)s.raw.xz |"
                             "xz --decompress --stdout "
                             "> /dev/sheepdog/%(base)s" %
                             {"base": base_img, "url": img_url})
            self.log_success("Done installing base image")

        self.log_success("Creating %s" % disk)
        self.run_handler("lvcreate -s -L10G -n %(img)s sheepdog/%(base)s" %
                         {'base': base_img, 'img': basename(disk)})

        self.log_success("Done reimaging OS on %s" % instance.id)
