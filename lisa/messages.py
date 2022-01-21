from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from lisa.util import constants


@dataclass
class MessageBase:
    type: str = "Base"
    elapsed: float = 0


TestRunStatus = Enum(
    "TestRunStatus",
    [
        "INITIALIZING",
        "RUNNING",
        "SUCCESS",
        "FAILED",
    ],
)


@dataclass
class TestRunMessage(MessageBase):
    type: str = "TestRun"
    status: TestRunStatus = TestRunStatus.INITIALIZING
    test_project: str = ""
    test_pass: str = ""
    tags: Optional[List[str]] = None
    run_name: str = ""
    message: str = ""


class NetworkProtocol(str, Enum):
    IPv4 = "IPv4"
    IPv6 = "IPv6"


class TransportProtocol(str, Enum):
    Tcp = "TCP"
    Udp = "UDP"


@dataclass
class PerfMessage(MessageBase):
    type: str = "Performance"
    test_case_name: str = ""
    platform: str = ""
    location: str = ""
    host_version: str = ""
    guest_os_type: str = "Linux"
    distro_version: str = ""
    vmsize: str = ""
    kernel_version: str = ""
    lis_version: str = ""
    ip_version: str = NetworkProtocol.IPv4
    protocol_type: str = TransportProtocol.Tcp
    data_path: str = ""
    test_date: datetime = datetime.utcnow()


DiskSetupType = Enum(
    "DiskSetupType",
    [
        "raw",
        "raid0",
    ],
)


DiskType = Enum(
    "DiskType",
    [
        "nvme",
        "premiumssd",
    ],
)


@dataclass
class DiskPerformanceMessage(PerfMessage):
    tool: str = constants.DISK_PERFORMANCE_TOOL
    disk_setup_type: DiskSetupType = DiskSetupType.raw
    block_size: int = 0
    disk_type: DiskType = DiskType.nvme
    core_count: int = 0
    disk_count: int = 0
    qdepth: int = 0
    iodepth: int = 0
    numjob: int = 0
    read_iops: Decimal = Decimal(0)
    read_lat_usec: Decimal = Decimal(0)
    randread_iops: Decimal = Decimal(0)
    randread_lat_usec: Decimal = Decimal(0)
    write_iops: Decimal = Decimal(0)
    write_lat_usec: Decimal = Decimal(0)
    randwrite_iops: Decimal = Decimal(0)
    randwrite_lat_usec: Decimal = Decimal(0)


@dataclass
class NetworkLatencyPerformanceMessage(PerfMessage):
    max_latency_us: Decimal = Decimal(0)
    average_latency_us: Decimal = Decimal(0)
    min_latency_us: Decimal = Decimal(0)
    latency95_percentile_us: Decimal = Decimal(0)
    latency99_percentile_us: Decimal = Decimal(0)
    interval_us: int = 0
    frequency: int = 0


@dataclass
class NetworkPPSPerformanceMessage(PerfMessage):
    test_type: str = ""
    rx_pps_minimum: Decimal = Decimal(0)
    rx_pps_average: Decimal = Decimal(0)
    rx_pps_maximum: Decimal = Decimal(0)
    tx_pps_minimum: Decimal = Decimal(0)
    tx_pps_average: Decimal = Decimal(0)
    tx_pps_maximum: Decimal = Decimal(0)
    rx_tx_pps_minimum: Decimal = Decimal(0)
    rx_tx_pps_average: Decimal = Decimal(0)
    rx_tx_pps_maximum: Decimal = Decimal(0)


@dataclass
class NetworkNtttcpTCPPerformanceMessage(PerfMessage):
    connections_num: int = 0
    throughput_in_gbps: Decimal = Decimal(0)
    latency_us: Decimal = Decimal(0)
    buffer_size: Decimal = Decimal(0)
    tx_packets: Decimal = Decimal(0)
    rx_packets: Decimal = Decimal(0)
    pkts_interrupts: Decimal = Decimal(0)
    number_of_receivers: int = 1
    number_of_senders: int = 1
    sender_cycles_per_byte: Decimal = Decimal(0)
    connections_created_time: int = 0
    retrans_segments: int = 0
    receiver_cycles_rer_byte: Decimal = Decimal(0)


@dataclass
class NetworkNtttcpUDPPerformanceMessage(PerfMessage):
    connections_num: int = 0
    number_of_receivers: int = 1
    number_of_senders: int = 1
    connections_created_time: int = 0
    receiver_cycles_rer_byte: Decimal = Decimal(0)
    send_buffer_size: Decimal = Decimal(0)
    tx_throughput_in_gbps: Decimal = Decimal(0)
    rx_throughput_in_gbps: Decimal = Decimal(0)
    data_loss: Decimal = Decimal(0)
    packet_size_kbytes: Decimal = Decimal(0)


@dataclass
class NetworkIperfSingleTCPPerformanceMessage(PerfMessage):
    buffer_size_bytes: Decimal = Decimal(0)
    tx_throughput_in_gbps: Decimal = Decimal(0)
    rx_throughput_in_gbps: Decimal = Decimal(0)
    retransmitted_segments: Decimal = Decimal(0)
    congestion_windowsize_kb: Decimal = Decimal(0)


@dataclass
class NetworkIperfUDPPerformanceMessage(NetworkNtttcpUDPPerformanceMessage):
    pass
