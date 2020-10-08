# Copyright 2020 Cloudbase Solutions Srl
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

from sushy_tools.emulator.resources.systems.base import AbstractSystemsDriver
from sushy_tools import error

try:
    from os_win import utilsfactory
    from os_win import constants
except ImportError:
    utilsfactory = None

is_loaded = bool(utilsfactory)

_BOOT_SOURCE_TYPE_DRIVE = 1
_BOOT_SOURCE_TYPE_NETWORK = 2


class HyperVDriver(AbstractSystemsDriver):
    """Base class for all virtualization drivers"""

    @classmethod
    def initialize(cls, config, logger, *args, **kwargs):
        """Initialize class attribute."""
        cls._config = config
        cls._logger = logger

        cls._vmutils = utilsfactory.get_vmutils()

        cls._VM_STATE_MAP = {
            "On": constants.HYPERV_VM_STATE_ENABLED,
            "ForceOn": constants.HYPERV_VM_STATE_ENABLED,
            "ForceOff": constants.HYPERV_VM_STATE_DISABLED,
            "ForceRestart": constants.HYPERV_VM_STATE_REBOOT,
        }

        cls._BOOT_ORDER_MAP = {
            "Pxe": constants.BOOT_DEVICE_NETWORK,
            "Hdd": constants.BOOT_DEVICE_HARDDISK,
            "Cd": constants.BOOT_DEVICE_CDROM,
        }

        return cls

    @property
    def driver(self):
        """Return human-friendly driver information

        :returns: driver information as `str`
        """
        return '<hyperv>'

    @property
    def systems(self):
        """Return available computer systems

        :returns: list of UUIDs representing the systems
        """
        return self._vmutils.list_instances()

    def uuid(self, identity):
        """Get computer system UUID

        The universal unique identifier (UUID) for this system. Can be used
        in place of system name if there are duplicates.

        If virtualization backend does not support non-unique system identity,
        this method may just return the `identity`.

        :returns: computer system UUID
        """
        return self._vmutils.get_vm_id(identity)

    def name(self, identity):
        """Get computer system name by UUID

        The universal unique identifier (UUID) for this system. Can be used
        in place of system name if there are duplicates.

        If virtualization backend does not support system names
        this method may just return the `identity`.

        :returns: computer system name
        """
        return identity

    def get_power_state(self, identity):
        """Get computer system power state

        :returns: current power state as *On* or *Off* `str` or `None`
            if power state can't be determined
        """

        state = self._vmutils.get_vm_state(identity)
        return "On" if state == constants.HYPERV_VM_STATE_ENABLED else "Off"

    def set_power_state(self, identity, state):
        """Set computer system power state

        :param state: string literal requesting power state transition.
            Valid values  are: *On*, *ForceOn*, *ForceOff*, *GracefulShutdown*,
            *GracefulRestart*, *ForceRestart*, *Nmi*.

        :raises: `FishyError` if power state can't be set
        """
        try:
            vm_state = self._VM_STATE_MAP.get(state)
            if vm_state:
                self._vmutils.set_vm_state(identity, vm_state)
            else:
                if state == "GracefulShutdown":
                    self._vmutils.soft_shutdown_vm(identity)
                else:
                    raise error.NotSupportedError()
        except error.FishyError:
            raise
        except Exception as ex:
            raise error.FishyError(
                "Failed to set the power state \"%(state)s\" "
                "for Hyper-V VM \"%(vm)s\": %(ex)s" %
                {"state": state, "vm": identity, "ex": ex})

    def _get_boot_device_gen2(self, identity):
        boot_devices = list(
            self._vmutils._lookup_vm_check(identity).BootSourceOrder)

        if not boot_devices:
            return

        bssd = self._vmutils._get_wmi_obj(boot_devices[0])

        if bssd.BootSourceType == _BOOT_SOURCE_TYPE_NETWORK:
            return "Pxe"
        elif bssd.BootSourceType == _BOOT_SOURCE_TYPE_DRIVE:
            rasd = bssd.associators(
                wmi_association_class=self._vmutils._LOGICAL_IDENTITY_CLASS)[0]

            if rasd.ResourceSubType == self._vmutils._DISK_DRIVE_RES_SUB_TYPE:
                return "Hdd"
            elif rasd.ResourceSubType == self._vmutils._DVD_DRIVE_RES_SUB_TYPE:
                return "Cd"

    def _get_boot_device_gen1(self, identity):
        boot_order = list(self._vmutils._lookup_vm_check(identity).BootOrder)
        return next(
            k for (k, v) in self._BOOT_ORDER_MAP.items() if v == boot_order[0])

    def get_boot_device(self, identity):
        """Get computer system boot device name

        :returns: boot device name as `str` or `None` if device name
            can't be determined
        """
        if self._vmutils.get_vm_generation(identity) == constants.VM_GEN_1:
            return self._get_boot_device_gen1(identity)
        else:
            return self._get_boot_device_gen2(identity)

    def _set_boot_device_gen1(self, identity, boot_source):
        boot_order = list(self._vmutils._lookup_vm_check(identity).BootOrder)
        boot_device = self._BOOT_ORDER_MAP.get(boot_source)
        boot_order.remove(boot_device)
        boot_order.insert(0, boot_device)

        self._vmutils.set_boot_order(identity, boot_order)

    def _set_boot_device_gen2(self, identity, boot_source):
        vssd = self._vmutils._lookup_vm_check(identity)
        old_boot_devices = list(vssd.BootSourceOrder)

        new_boot_devices = []

        for boot_device in old_boot_devices:
            bssd = self._vmutils._get_wmi_obj(boot_device)

            if (boot_source == "Pxe"
                    and bssd.BootSourceType == _BOOT_SOURCE_TYPE_NETWORK):
                new_boot_devices.append(boot_device)
            else:
                rasd = bssd.associators(
                    wmi_association_class=self._vmutils._LOGICAL_IDENTITY_CLASS
                    )[0]

                if (boot_source == "Hdd"
                        and rasd.ResourceSubType
                        == self._vmutils._DISK_DRIVE_RES_SUB_TYPE
                        or boot_source == "Cd"
                        and rasd.ResourceSubType
                        == self._vmutils._DVD_DRIVE_RES_SUB_TYPE):
                    new_boot_devices.append(boot_device)

        new_boot_devices += [
            d for d in old_boot_devices if d not in new_boot_devices]

        vssd.BootSourceOrder = tuple(new_boot_devices)
        self._vmutils._modify_virtual_system(vssd)

    def set_boot_device(self, identity, boot_source):
        """Set computer system boot device name

        :param boot_source: string literal requesting boot device change on the
            system. Valid values are: *Pxe*, *Hdd*, *Cd*.

        :raises: `FishyError` if boot device can't be set
        """
        if self._vmutils.get_vm_generation(identity) == constants.VM_GEN_1:
            self._set_boot_device_gen1(identity, boot_source)
        else:
            self._set_boot_device_gen2(identity, boot_source)

    def get_boot_mode(self, identity):
        """Get computer system boot mode.

        :returns: either *UEFI* or *Legacy* as `str` or `None` if
            current boot mode can't be determined
        """
        vm_gen = self._vmutils.get_vm_generation(identity)
        return "UEFI" if vm_gen == constants.VM_GEN_2 else "Legacy"

    def set_boot_mode(self, identity, boot_mode):
        """Set computer system boot mode.

        :param boot_mode: string literal requesting boot mode
            change on the system. Valid values are: *UEFI*, *Legacy*.

        :raises: `FishyError` if boot mode can't be set
        """
        pass

    def get_total_memory(self, identity):
        """Get computer system total memory

        :returns: available RAM in GiB as `int` or `None` if total memory
            count can't be determined
        """
        return self._vmutils.get_vm_summary_info(identity).get("MemoryUsage")

    def get_total_cpus(self, identity):
        """Get computer system total count of available CPUs

        :returns: available CPU count as `int` or `None` if CPU count
            can't be determined
        """
        return self._vmutils.get_vm_summary_info(identity).get(
            "NumberOfProcessors")

    def get_bios(self, identity):
        """Get BIOS attributes for the system

        :returns: key-value pairs of BIOS attributes

        :raises: `FishyError` if BIOS attributes cannot be processed
        """
        raise error.NotSupportedError()

    def set_bios(self, identity, attributes):
        """Update BIOS attributes

        :param attributes: key-value pairs of attributes to update

        :raises: `FishyError` if BIOS attributes cannot be processed
        """
        raise error.NotSupportedError()

    def reset_bios(self, identity):
        """Reset BIOS attributes to default

        :raises: `FishyError` if BIOS attributes cannot be processed
        """
        raise error.NotSupportedError()

    def get_nics(self, identity):
        """Get list of NICs and their attributes

        :returns: list of dictionaries of NICs and their attributes
        """
        nics = self._vmutils._get_vm_nics(identity)
        return [{"id": n.ElementName, "mac": n.Address} for n in nics]

    def get_boot_image(self, identity, device):
        """Get backend VM boot image info

        :param identity: node name or ID
        :param device: device type (from
            `sushy_tools.emulator.constants`)
        :returns: a `tuple` of (boot_image, write_protected, inserted)
        :raises: `error.FishyError` if boot device can't be accessed
        """
        raise error.NotSupportedError()

    def set_boot_image(self, identity, device, boot_image=None,
                       write_protected=True):
        """Set backend VM boot image

        :param identity: node name or ID
        :param device: device type (from
            `sushy_tools.emulator.constants`)
        :param boot_image: path to the image file or `None` to remove
            configured image entirely
        :param write_protected: expose media as read-only or writable

        :raises: `error.FishyError` if boot device can't be set
        """
        raise error.NotSupportedError()

    def get_simple_storage_collection(self, identity):
        """Get a dict of Simple Storage Controllers and their devices

        :returns: dict of Simple Storage Controllers and their atributes
        """
        return {}

    def find_or_create_storage_volume(self, data):
        """Find/create volume based on existence in the virtualization backend

        :param data: data about the volume in dict form with values for `Id`,
                     `Name`, `CapacityBytes`, `VolumeType`, `libvirtPoolName`
                     and `libvirtVolName`

        :returns: Id of the volume if successfully found/created else None
        """
        pass
