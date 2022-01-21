# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from decimal import Decimal
from typing import List, Type, cast

from lisa.executable import Tool
from lisa.messages import (
    NetworkIperfSingleTCPPerformanceMessage,
    NetworkIperfUDPPerformanceMessage,
    TransportProtocol,
)
from lisa.operating_system import Posix
from lisa.util.process import ExecutableResult, Process

from .git import Git
from .make import Make

IPERF_TCP_BUFFER_LENGTHS = [
    32,
    64,
    128,
    256,
    512,
    1024,
    2048,
    4096,
    8192,
    16384,
    32768,
    65536,
    131072,
    262144,
    524288,
    1048576,
]
IPERF_UDP_BUFFER_LENGTHS = [1024, 8192]
IPERF_TCP_CONCURRENCY = [1]
IPERF_UDP_CONCURRENCY = [
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
    512,
    1024,
]


class Iperf3(Tool):
    repo = "https://github.com/esnet/iperf"
    branch = "3.10.1"

    @property
    def command(self) -> str:
        return "iperf3"

    @property
    def can_install(self) -> bool:
        return True

    @property
    def dependencies(self) -> List[Type[Tool]]:
        return [Git, Make]

    def _install_from_src(self) -> None:
        tool_path = self.get_tool_path()
        git = self.node.tools[Git]
        git.clone(self.repo, tool_path)
        code_path = tool_path.joinpath("iperf")
        make = self.node.tools[Make]
        self.node.execute("./configure", cwd=code_path).assert_exit_code()
        make.make_install(code_path)
        self.node.execute("ldconfig", sudo=True, cwd=code_path).assert_exit_code()
        self.node.execute(
            "ln -s /usr/local/bin/iperf3 /usr/bin/iperf3", sudo=True, cwd=code_path
        ).assert_exit_code()

    def install(self) -> bool:
        posix_os: Posix = cast(Posix, self.node.os)
        posix_os.install_packages("iperf3")
        if not self._check_exists():
            self._install_from_src()
        return self._check_exists()

    def run_as_server_async(
        self,
        port: int = 0,
        format: str = "",
        interval_seconds: int = 0,
        use_json_format: bool = False,
        one_connection_only: bool = False,
        daemon: bool = True,
    ) -> Process:
        # -s: run iperf3 as server mode
        # -D: run iperf3 as a daemon
        cmd = " -s"
        if daemon:
            cmd += " -D "
        if one_connection_only:
            cmd += " -1 "
        if use_json_format:
            cmd += " -J "
        if interval_seconds:
            cmd += f" -i{interval_seconds} "
        if format:
            cmd += f" -f {format} "
        if port:
            cmd += f" -p {port} "
        process = self.node.execute_async(
            f"{self.command} {cmd}", shell=True, sudo=True
        )
        return process

    def run_as_server(
        self,
        seconds: int = 10,
        port: int = 0,
        format: str = "",
        interval_seconds: int = 0,
        use_json_format: bool = False,
        one_connection_only: bool = False,
        daemon: bool = True,
    ) -> None:
        # -s: run iperf3 as server mode
        # -D: run iperf3 as a daemon
        # -p: server port to listen on/connect to
        # -f: [kmgtKMGT] format to report: Kbits, Mbits, Gbits, Tbits
        # -i: seconds between periodic throughput reports
        # -1: handle one client connection then exit
        # -J: output in JSON format
        process = self.run_as_server_async(
            port, format, interval_seconds, use_json_format, one_connection_only, daemon
        )
        process.wait_result(
            expected_exit_code=0,
            expected_exit_code_failure_message="fail to lanuch iperf3 server",
        )

    def run_as_client_async(
        self,
        server_ip: str,
        bitrate: str = "",
        output_json: bool = False,
        interval_seconds: int = 0,
        format: str = "",
        port: int = 0,
        buffer_length: int = 0,
        log_file: str = "",
        seconds: int = 10,
        parallel_number: int = 0,
        client_ip: str = "",
        ip_version: str = "",
        udp_mode: bool = False,
    ) -> Process:
        # -c: run iperf3 as client mode, followed by iperf3 server ip address
        # -t: run iperf3 testing for given seconds
        # --logfile: save logs into specified file
        # -b, --bitrate #[KMG][/#]  target bitrate in bits/sec (0 for unlimited)
        # (default 1 Mbit/sec for UDP, unlimited for TCP)
        # (optional slash and packet count for burst mode)
        # -J: output in JSON format
        # -f: [kmgtKMGT] format to report: Kbits, Mbits, Gbits, Tbits
        # -i: seconds between periodic throughput reports
        # -l: length of buffer to read or write
        # (default 128 KB for TCP, dynamic or 1460 for UDP)
        # -p: server port to listen on/connect to
        # -P: number of parallel client streams to run
        # -4: only use IPv4
        # -6: only use IPv6
        cmd = f"-t {seconds} -c {server_ip}"
        if udp_mode:
            cmd += " -u "
        if bitrate:
            cmd += f" -b {bitrate}"
        if output_json:
            cmd += " -J "
        if interval_seconds:
            cmd += f" -i{interval_seconds} "
        if format:
            cmd += f" -f {format} "
        if port:
            cmd += f" -p {port} "
        if parallel_number:
            cmd += f" -P {parallel_number}"
        if client_ip:
            cmd += f" -B {client_ip}"
        if buffer_length:
            cmd += f" -l {buffer_length} "
        if ip_version == "4":
            cmd += " -4 "
        if ip_version == "6":
            cmd += " -6 "
        if log_file:
            if self.node.shell.exists(self.node.get_pure_path(log_file)):
                self.node.shell.remove(self.node.get_pure_path(log_file))
            cmd += f" --logfile {log_file}"
        process = self.node.execute_async(
            f"{self.command} {cmd}", shell=True, sudo=True
        )
        return process

    def run_as_client(
        self,
        server_ip: str,
        bitrate: str = "",
        output_json: bool = False,
        interval_seconds: int = 0,
        format: str = "",
        port: int = 0,
        buffer_length: int = 0,
        log_file: str = "",
        seconds: int = 10,
        parallel_number: int = 0,
        client_ip: str = "",
        ip_version: str = "",
        udp_mode: bool = False,
    ) -> ExecutableResult:
        process = self.run_as_client_async(
            server_ip,
            bitrate,
            output_json,
            interval_seconds,
            format,
            port,
            buffer_length,
            log_file,
            seconds,
            parallel_number,
            client_ip,
            ip_version,
            udp_mode,
        )
        return process.wait_result(
            expected_exit_code=0,
            expected_exit_code_failure_message="fail to lanuch iperf3 client",
        )

    def create_iperf_tcp_performance_message(
        self,
        server_result: str,
        client_result: str,
        buffer_length: int,
    ) -> NetworkIperfSingleTCPPerformanceMessage:
        server_json = json.loads(server_result)
        client_json = json.loads(client_result)
        congestion_windowsize_kb_total: Decimal = Decimal(0)
        for client_interval in client_json["intervals"]:
            streams = client_interval["streams"]
            congestion_windowsize_kb_total += streams[0]["snd_cwnd"]
        message = NetworkIperfSingleTCPPerformanceMessage()
        message.buffer_size_bytes = Decimal(buffer_length)
        message.rx_throughput_in_gbps = (
            server_json["end"]["sum_received"]["bits_per_second"] / 1000000000
        )
        message.tx_throughput_in_gbps = (
            client_json["end"]["sum_received"]["bits_per_second"] / 1000000000
        )
        message.congestion_windowsize_kb = (
            congestion_windowsize_kb_total / len(client_json["intervals"]) / 1024
        )
        for client_stream in client_json["end"]["streams"]:
            message.retransmitted_segments = client_stream["sender"]["retransmits"]
        return message

    def create_iperf_udp_performance_message(
        self,
        server_result_list: List[ExecutableResult],
        client_result_list: List[ExecutableResult],
        buffer_length: int,
        connections_num: int,
    ) -> NetworkIperfUDPPerformanceMessage:
        client_udp_lost_list: List[Decimal] = []
        client_intervals_throughput_list: List[Decimal] = []
        client_throughput_list: List[Decimal] = []
        for client_result_raw in client_result_list:
            client_result = json.loads(client_result_raw.stdout)
            if (
                "sum" in client_result["end"].keys()
                and "lost_percent" in client_result["end"]["sum"].keys()
            ):
                client_udp_lost_list.append(
                    Decimal(client_result["end"]["sum"]["lost_percent"])
                )
                for client_interval in client_result["intervals"]:
                    client_intervals_throughput_list.append(
                        client_interval["sum"]["bits_per_second"]
                    )
                client_throughput_list.append(
                    (
                        Decimal(
                            sum(client_intervals_throughput_list)
                            / len(client_intervals_throughput_list)
                        )
                        / 1000000000
                    )
                )
        server_udp_lost_list: List[Decimal] = []
        server_intervals_throughput_list: List[Decimal] = []
        server_throughput_list: List[Decimal] = []
        for server_result_raw in server_result_list:
            server_result = json.loads(server_result_raw.stdout)
            if (
                "sum" in server_result["end"].keys()
                and "lost_percent" in server_result["end"]["sum"].keys()
            ):
                server_udp_lost_list.append(
                    Decimal(server_result["end"]["sum"]["lost_percent"])
                )
                for server_interval in server_result["intervals"]:
                    server_intervals_throughput_list.append(
                        server_interval["sum"]["bits_per_second"]
                    )
                server_throughput_list.append(
                    (
                        Decimal(
                            sum(server_intervals_throughput_list)
                            / len(server_intervals_throughput_list)
                        )
                        / 1000000000
                    )
                )

        message = NetworkIperfUDPPerformanceMessage()
        message.tx_throughput_in_gbps = Decimal(
            sum(client_throughput_list) / len(client_throughput_list)
        )
        message.data_loss = Decimal(
            sum(client_udp_lost_list) / len(client_udp_lost_list)
        )
        message.rx_throughput_in_gbps = Decimal(
            sum(server_throughput_list) / len(server_throughput_list)
        )
        message.send_buffer_size = Decimal(buffer_length)
        message.connections_num = connections_num
        message.protocol_type = TransportProtocol.Udp
        return message
