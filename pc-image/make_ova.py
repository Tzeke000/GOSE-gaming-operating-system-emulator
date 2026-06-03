#!/usr/bin/env python3
"""Package a raw GOSE-PC disk image into an importable OVA (VirtualBox/VMware).

The OVF descriptor generation is pure and unit-tested (agent/tests/test_ova.py).
Converting the raw .img to a streamOptimized .vmdk needs `qemu-img` and is
marked [needs qemu]; once converted, `write_ova()` tars the OVF + disk + manifest
into a single .ova a user can double-click to import.
"""
from __future__ import annotations
import argparse
import hashlib
import os
import subprocess
import sys
import tarfile

OVF_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
  xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
  xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
  xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <References>
    <File ovf:href="{disk}" ovf:id="file1" ovf:size="{disk_size}"/>
  </References>
  <DiskSection>
    <Info>Virtual disks</Info>
    <Disk ovf:capacity="{capacity}" ovf:diskId="vmdisk1" ovf:fileRef="file1"
      ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
  </DiskSection>
  <NetworkSection>
    <Info>Networks</Info>
    <Network ovf:name="NAT"><Description>NAT — forwards the GOSE agent on 5555</Description></Network>
  </NetworkSection>
  <VirtualSystem ovf:id="{name}">
    <Info>GOSE-PC virtual machine</Info>
    <Name>{name}</Name>
    <OperatingSystemSection ovf:id="100" ovf:version="0" vmw:osType="other4xLinux64Guest"
      xmlns:vmw="http://www.vmware.com/schema/ovf"><Info>Linux x86_64 (Batocera + GOSE)</Info></OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual hardware requirements</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemType>virtualbox-2.2</vssd:VirtualSystemType>
      </System>
      <Item>
        <rasd:Caption>{cpus} virtual CPU</rasd:Caption>
        <rasd:Description>Number of virtual CPUs</rasd:Description>
        <rasd:ElementName>{cpus} virtual CPU</rasd:ElementName>
        <rasd:InstanceID>1</rasd:InstanceID>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>{cpus}</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits>
        <rasd:Caption>{memory} MB of memory</rasd:Caption>
        <rasd:ElementName>{memory} MB of memory</rasd:ElementName>
        <rasd:InstanceID>2</rasd:InstanceID>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>{memory}</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Address>0</rasd:Address>
        <rasd:Caption>sataController0</rasd:Caption>
        <rasd:ElementName>sataController0</rasd:ElementName>
        <rasd:InstanceID>3</rasd:InstanceID>
        <rasd:ResourceSubType>AHCI</rasd:ResourceSubType>
        <rasd:ResourceType>20</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:Caption>disk1</rasd:Caption>
        <rasd:ElementName>disk1</rasd:ElementName>
        <rasd:HostResource>/disk/vmdisk1</rasd:HostResource>
        <rasd:InstanceID>4</rasd:InstanceID>
        <rasd:Parent>3</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
"""


def build_ovf(name, disk, disk_size, capacity, memory_mb, cpus):
    """Return the OVF XML for a single-disk GOSE-PC VM."""
    return OVF_TEMPLATE.format(name=name, disk=disk, disk_size=int(disk_size),
                               capacity=int(capacity), memory=int(memory_mb), cpus=int(cpus))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(paths):
    """OVF .mf manifest: 'SHA256(name)= digest' per file."""
    return "".join(f"SHA256({os.path.basename(p)})= {_sha256(p)}\n" for p in paths)


def write_ova(out_path, ovf_path, disk_path):
    """Tar OVF + disk + manifest into an .ova (OVF must be the first member)."""
    base = os.path.splitext(os.path.basename(ovf_path))[0]
    mf_path = os.path.join(os.path.dirname(ovf_path) or ".", base + ".mf")
    with open(mf_path, "w") as f:
        f.write(build_manifest([ovf_path, disk_path]))
    with tarfile.open(out_path, "w") as tar:  # uncompressed, per OVA spec
        tar.add(ovf_path, arcname=os.path.basename(ovf_path))   # 1st: descriptor
        tar.add(disk_path, arcname=os.path.basename(disk_path))
        tar.add(mf_path, arcname=os.path.basename(mf_path))
    return out_path


def convert_to_vmdk(img_path, vmdk_path):  # pragma: no cover - [needs qemu]
    subprocess.check_call(["qemu-img", "convert", "-O", "vmdk",
                           "-o", "subformat=streamOptimized", img_path, vmdk_path])


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Package GOSE-PC image into an OVA.")
    ap.add_argument("--image", help="raw .img (converted to vmdk) or an existing .vmdk")
    ap.add_argument("--name", default="GOSE-PC")
    ap.add_argument("--memory", type=int, default=6144, help="MB")
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--capacity", type=int, default=32 * 1024**3, help="virtual disk bytes")
    ap.add_argument("--out", default="GOSE-PC.ova")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        ovf = build_ovf("GOSE-PC", "GOSE-PC-disk1.vmdk", 1234, 32 * 1024**3, 6144, 4)
        assert "<Name>GOSE-PC</Name>" in ovf
        assert 'ovf:href="GOSE-PC-disk1.vmdk"' in ovf
        assert "<rasd:VirtualQuantity>6144</rasd:VirtualQuantity>" in ovf
        assert "<rasd:VirtualQuantity>4</rasd:VirtualQuantity>" in ovf
        print("self-test OK")
        return 0

    if not a.image:
        ap.error("--image is required (or use --self-test)")
    if a.image.endswith(".vmdk"):
        vmdk = a.image
    else:
        vmdk = os.path.splitext(a.image)[0] + ".vmdk"
        print(f"converting {a.image} -> {vmdk} (qemu-img)…")
        convert_to_vmdk(a.image, vmdk)  # [needs qemu]
    ovf_path = a.name + ".ovf"
    with open(ovf_path, "w") as f:
        f.write(build_ovf(a.name, os.path.basename(vmdk), os.path.getsize(vmdk),
                          a.capacity, a.memory, a.cpus))
    write_ova(a.out, ovf_path, vmdk)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
