# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
from typing import Dict, List, cast

from assertpy import assert_that
from retry import retry

from lisa import Environment, Node, RemoteNode, constants
from lisa.features import NetworkInterface
from lisa.nic import NicInfo, Nics
from lisa.tools import Cat, Firewall, Kill, Lsmod, Lspci, Modprobe, Ssh

# ConnectX-3 uses mlx4_core
# mlx4_en and mlx4_ib depends on mlx4_core
#  need remove mlx4_en and mlx4_ib firstly
#  otherwise will see modules is in used issue
# ConnectX-4/ConnectX-5 uses mlx5_core
# mlx5_ib depends on mlx5_core, need remove mlx5_ib firstly
reload_modules_dict: Dict[str, List[str]] = {
    "mlx5_core": ["mlx5_ib"],
    "mlx4_core": ["mlx4_en", "mlx4_ib"],
}


@retry(exceptions=AssertionError, tries=150, delay=2)  # type: ignore
def initialize_nic_info(environment: Environment) -> Dict[str, Dict[str, NicInfo]]:
    vm_nics: Dict[str, Dict[str, NicInfo]] = {}
    for node in environment.nodes.list():
        node_nic_info = Nics(node)
        node_nic_info.initialize()
        for _, node_nic in node_nic_info.nics.items():
            assert_that(node_nic.lower).described_as(
                f"This interface {node_nic.upper} does not have a paired VF."
            ).is_not_empty()
        vm_nics[node.name] = node_nic_info.nics
    return vm_nics


def remove_module(node: Node) -> str:
    lspci = node.tools[Lspci]
    modprobe = node.tools[Modprobe]
    devices_slots = lspci.get_devices_slots(constants.DEVICE_TYPE_SRIOV, force_run=True)
    # there will not be multiple Mellanox types in one VM
    # get the used module using any one of sriov device
    module_in_used = lspci.get_used_module(devices_slots[0])
    assert_that(reload_modules_dict).described_as(
        f"used modules {module_in_used} should be contained"
        f" in dict {reload_modules_dict}"
    ).contains(module_in_used)
    modprobe.remove(reload_modules_dict[module_in_used])
    return module_in_used


def load_module(node: Node, module_name: str) -> None:
    modprobe = node.tools[Modprobe]
    modprobe.load(module_name)


def get_packets(node: Node, nic_name: str, name: str = "tx_packets") -> int:
    cat = node.tools[Cat]
    return int(cat.read(f"/sys/class/net/{nic_name}/statistics/{name}", force_run=True))


@retry(exceptions=AssertionError, tries=150, delay=2)  # type: ignore
def sriov_basic_test(
    environment: Environment, vm_nics: Dict[str, Dict[str, NicInfo]]
) -> None:
    for node in environment.nodes.list():
        # 1. Check module of sriov network device is loaded.
        modules_exist = False
        lsmod = node.tools[Lsmod]
        for module in ["mlx4_core", "mlx4_en", "mlx5_core", "ixgbevf"]:
            if lsmod.module_exists(module):
                modules_exist = True
        assert_that(modules_exist).described_as(
            "The module of sriov network device isn't loaded."
        ).is_true()

        # 2. Check VF counts listed from lspci is expected.
        lspci = node.tools[Lspci]
        devices_slots = lspci.get_devices_slots(
            constants.DEVICE_TYPE_SRIOV, force_run=True
        )
        assert_that(devices_slots).described_as(
            "count of sriov devices listed from lspci is not expected,"
            " please check the driver works properly"
        ).is_length(len(vm_nics[node.name]))


def sriov_vf_connection_test(
    environment: Environment,
    vm_nics: Dict[str, Dict[str, NicInfo]],
    turn_off_vf: bool = False,
    remove_module: bool = False,
) -> None:
    source_node = cast(RemoteNode, environment.nodes[0])
    dest_node = cast(RemoteNode, environment.nodes[1])
    source_ssh = source_node.tools[Ssh]
    dest_ssh = dest_node.tools[Ssh]

    dest_ssh.enable_public_key(source_ssh.generate_key_pairs())
    # generate 200Mb file
    source_node.execute("dd if=/dev/urandom of=large_file bs=100 count=0 seek=2M")
    max_retry_times = 10
    for _, source_nic_info in vm_nics[source_node.name].items():
        matched_dest_nic_name = ""
        for dest_nic_name, dest_nic_info in vm_nics[dest_node.name].items():
            # only when IPs are in the same subnet, IP1 of machine A can connect to
            # IP2 of machine B
            # e.g. eth2 IP is 10.0.2.3 on machine A, eth2 IP is 10.0.3.4 on machine
            # B, use nic name doesn't work in this situation
            if (
                dest_nic_info.ip_addr.rsplit(".", maxsplit=1)[0]
                == source_nic_info.ip_addr.rsplit(".", maxsplit=1)[0]
            ):
                matched_dest_nic_name = dest_nic_name
                break
        assert_that(matched_dest_nic_name).described_as(
            f"can't find the same subnet nic with {source_nic_info.ip_addr} on"
            f" machine {source_node.name}, please check network setting of "
            f"machine {dest_node.name}."
        ).is_not_empty()
        desc_nic_info = vm_nics[dest_node.name][matched_dest_nic_name]
        dest_ip = vm_nics[dest_node.name][matched_dest_nic_name].ip_addr
        source_ip = source_nic_info.ip_addr
        source_synthetic_nic = source_nic_info.upper
        dest_synthetic_nic = desc_nic_info.upper
        source_nic = source_vf_nic = source_nic_info.lower
        dest_nic = dest_vf_nic = desc_nic_info.lower

        if remove_module or turn_off_vf:
            source_nic = source_synthetic_nic
            dest_nic = dest_synthetic_nic
        if turn_off_vf:
            source_node.execute(f"ip link set dev {source_vf_nic} down", sudo=True)
            dest_node.execute(f"ip link set dev {dest_vf_nic} down", sudo=True)

        # get origin tx_packets and rx_packets before copy file
        source_tx_packets_origin = get_packets(source_node, source_nic)
        dest_tx_packets_origin = get_packets(dest_node, dest_nic, "rx_packets")

        # check the connectivity between source and dest machine using ping
        for _ in range(max_retry_times):
            cmd_result = source_node.execute(
                f"ping -c 1 {dest_ip} -I {source_synthetic_nic}"
            )
            if cmd_result.exit_code == 0:
                break
        cmd_result.assert_exit_code(
            message=f"fail to ping {dest_ip} from {source_node.name} to "
            f"{dest_node.name} after retry {max_retry_times}"
        )

        # copy 200 Mb file from source ip to dest ip
        cmd_result = source_node.execute(
            f"scp -o BindAddress={source_ip} -i ~/.ssh/id_rsa -o"
            f" StrictHostKeyChecking=no large_file "
            f"$USER@{dest_ip}:/tmp/large_file",
            shell=True,
            expected_exit_code=0,
            expected_exit_code_failure_message="Fail to copy file large_file from"
            f" {source_ip} to {dest_ip}",
        )
        source_tx_packets = get_packets(source_node, source_nic)
        dest_tx_packets = get_packets(dest_node, dest_nic, "rx_packets")
        # verify tx_packets value of source nic is increased after coping 200Mb file
        #  from source to dest
        assert_that(
            int(source_tx_packets), "insufficient TX packets sent"
        ).is_greater_than(int(source_tx_packets_origin))
        # verify rx_packets value of dest nic is increased after receiving 200Mb
        #  file from source to dest
        assert_that(
            int(dest_tx_packets), "insufficient RX packets received"
        ).is_greater_than(int(dest_tx_packets_origin))

        if turn_off_vf:
            source_node.execute(f"ip link set dev {source_vf_nic} up", sudo=True)
            dest_node.execute(f"ip link set dev {dest_vf_nic} up", sudo=True)


def stop_firewall(environment: Environment) -> None:
    for node in environment.nodes.list():
        firewall = node.tools[Firewall]
        firewall.stop()


def cleanup_iperf3(environment: Environment) -> None:
    for node in environment.nodes.list():
        kill = node.tools[Kill]
        kill.by_name("iperf3")


def remove_extra_nics(environment: Environment) -> None:
    for node in environment.nodes.list():
        node = cast(RemoteNode, environment.nodes[0])
        network_interface_feature = node.features[NetworkInterface]
        network_interface_feature.remove_extra_nics()
