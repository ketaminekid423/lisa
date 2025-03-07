# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from pathlib import PurePath, PurePosixPath
from typing import TYPE_CHECKING, List, Type

from retry import retry
from semver import VersionInfo

from lisa.base_tools import Cat, Sed, Wget
from lisa.executable import Tool
from lisa.operating_system import Debian, Posix, Redhat, Suse
from lisa.tools import Gcc
from lisa.tools.make import Make
from lisa.tools.service import Service
from lisa.tools.sysctl import Sysctl
from lisa.tools.tar import Tar
from lisa.util import LisaException, UnsupportedDistroException

if TYPE_CHECKING:
    from lisa.node import Node


class Kexec(Tool):
    """
    kexec - directly boot into a new kernel
    kexec is a system call that enables you to load and boot into another
    kernel from the currently running kernel. The primary difference between
    a standard system boot and a kexec boot is that the hardware initialization
    normally performed by the BIOS or firmware (depending on architecture)
    is not performed during a kexec boot. This has the effect of reducing the
    time required for a reboot.

    This tool is used for managing the installation of kexec.
    """

    # kexec-tools 2.0.16
    __pattern_kexec_version_info = re.compile(
        r"^kexec\S+\s+(?P<major>\d+).(?P<minor>\d+).(?P<patch>\d+)"
    )

    # Existed bug for kexec-tools 2.0.14
    # https://bugs.launchpad.net/ubuntu/+source/kexec-tools/+bug/1713940
    # If the version of kexec-tools is lower than 2.0.15, we install kexec from source
    _target_kexec_version = "2.0.15"

    # If install kexec from source, we choose 2.0.18 version for it is stable for most
    # Debian distros
    _kexec_repo = (
        "https://mirrors.edge.kernel.org/pub/linux/utils/kernel/kexec/"
        "kexec-tools-2.0.18.tar.gz"
    )

    @property
    def command(self) -> str:
        return "kexec"

    @property
    def can_install(self) -> bool:
        return True

    def _install(self) -> bool:
        assert isinstance(self.node.os, Posix)
        self.node.os.install_packages("kexec-tools")
        if isinstance(self.node.os, Debian):
            version = self._get_version()
            if version < self._target_kexec_version:
                self._install_from_src()
        return self._check_exists()

    def _get_version(self) -> VersionInfo:
        result = self.run(
            "-v",
            force_run=False,
            no_error_log=True,
            no_info_log=True,
            sudo=True,
            shell=False,
        )
        result.assert_exit_code(message=result.stderr)
        raw_version = re.finditer(self.__pattern_kexec_version_info, result.stdout)
        for version in raw_version:
            matched_version = self.__pattern_kexec_version_info.match(version.group())
            if matched_version:
                major = matched_version.group("major")
                minor = matched_version.group("minor")
                patch = matched_version.group("patch")
                self._log.info(f"kexec version is {major}.{minor}.{patch}")
                return VersionInfo(int(major), int(minor), int(patch))
        raise LisaException("No find matched kexec version")

    def _install_from_src(self) -> None:
        tool_path = self.get_tool_path()
        wget = self.node.tools[Wget]
        kexec_tar = wget.get(self._kexec_repo, str(tool_path))
        tar = self.node.tools[Tar]
        tar.extract(kexec_tar, str(tool_path))
        kexec_source = tar.get_root_folder(kexec_tar)
        code_path = tool_path.joinpath(kexec_source)
        self.node.tools[Gcc]
        make = self.node.tools[Make]
        self.node.execute("./configure", cwd=code_path).assert_exit_code()
        make.make_install(cwd=code_path)
        self.node.execute(
            "yes | cp -f /usr/local/sbin/kexec /sbin/", sudo=True, shell=True
        ).assert_exit_code()


class Makedumpfile(Tool):
    """
    makedumpfile - make a small dumpfile of kdump
    With kdump, the memory image of the first kernel can be taken as vmcore
    while the second kernel is running. makedumpfile makes a small DUMPFILE by
    compressing dump data or by excluding unnecessary pages for analysis, or both.

    This tool is used for managing the installation of makedumpfile.
    """

    @property
    def command(self) -> str:
        return "makedumpfile"

    @property
    def can_install(self) -> bool:
        return True

    def _install(self) -> bool:
        assert isinstance(self.node.os, Posix)
        if isinstance(self.node.os, Redhat):
            self.node.os.install_packages("kexec-tools")
        else:
            self.node.os.install_packages("makedumpfile")
        return self._check_exists()


class KdumpBase(Tool):
    """
    kdump is a feature of the Linux kernel that creates crash dumps in the event of a
    kernel crash. When triggered, kdump exports a memory image (also known as vmcore)
    that can be analyzed for the purposes of debugging and determining the cause of a
    crash.

    kdump tool manages the kdump feature of the Linux kernel. Different distro os has
    different kdump tool.

    KdumpBase is a basic class, it returns sub instance according to distros. We can
    support Redhat, Suse, Debian family distro now.
    """

    # If the file /sys/kernel/kexec_crash_loaded does not exist. This means that the
    # currently running kernel either was not configured to support kdump, or that a
    # crashkernel= commandline parameter was not used when the currently running kernel
    # booted. Value "1" means crash kernel is loaded, otherwise not loaded.
    #
    # It also has /sys/kernel/kexec_crash_size file, which record the crash kernel size
    # of memory reserved. We don't need to check this file in our test case.
    kexec_crash = "/sys/kernel/kexec_crash_loaded"

    # This file shows you the current map of the system's memory for each physical
    # device. We can check /proc/iomem file for memory reserved for crash kernel.
    iomem = "/proc/iomem"

    # Following are the configuration setting required for system and dump-capture
    # kernels for enabling kdump support.
    required_kernel_config = [
        "CONFIG_KEXEC",
        "CONFIG_CRASH_DUMP",
        "CONFIG_PROC_VMCORE",
    ]

    dump_path = "/var/crash"

    @classmethod
    def create(cls, node: "Node") -> Tool:
        if isinstance(node.os, Redhat):
            return KdumpRedhat(node)
        elif isinstance(node.os, Debian):
            return KdumpDebian(node)
        elif isinstance(node.os, Suse):
            return KdumpSuse(node)
        else:
            raise UnsupportedDistroException(os=node.os)

    @property
    def dependencies(self) -> List[Type[Tool]]:
        return [Kexec, Makedumpfile]

    @property
    def command(self) -> str:
        raise NotImplementedError()

    @property
    def can_install(self) -> bool:
        return True

    def _install(self) -> bool:
        raise NotImplementedError()

    def check_required_kernel_config(self, config_path: str) -> None:
        for config in self.required_kernel_config:
            result = self.node.execute(f"grep {config}=y {config_path}")
            result.assert_exit_code(
                message=f"The kernel config {config} is not set."
                "Kdump is not supported."
            )

    def _get_crashkernel_cfg_file(self) -> str:
        """
        This method return the path of cfg file where we config crashkernel memory.
        If distro has a different cfg file path, override it.
        """
        return "/etc/default/grub"

    def _get_crashkernel_update_cmd(self, crashkernel: str) -> str:
        """
        After setting crashkernel into grub cfg file, need updating grub configuration.
        This function returns the update command string. If distro has a different
        command, override this method.
        """
        return "grub2-mkconfig -o /boot/grub2/grub.cfg"

    def config_crashkernel_memory(
        self,
        crashkernel: str,
    ) -> None:

        # For Redhat 8 and later version, the cfg_file should be None.
        cfg_file = self._get_crashkernel_cfg_file()
        if cfg_file:
            assert self.node.shell.exists(PurePosixPath(cfg_file)), (
                f"{cfg_file} doesn't exist. Please check the right grub file for "
                f"{self.node.os.name} {self.node.os.information.version}."
            )
            cat = self.node.tools[Cat]
            sed = self.node.tools[Sed]
            result = cat.run(cfg_file)
            if "crashkernel" in result.stdout:
                sed.substitute(
                    match_lines="^GRUB_CMDLINE_LINUX",
                    regexp='crashkernel=[^[:space:]"]*',
                    replacement=f"crashkernel={crashkernel}",
                    file=cfg_file,
                    sudo=True,
                )
            else:
                sed.substitute(
                    match_lines="^GRUB_CMDLINE_LINUX",
                    regexp='"$',
                    replacement=f" crashkernel={crashkernel}",
                    file=cfg_file,
                    sudo=True,
                )
            # Check if crashkernel is insert in cfg file
            result = cat.run(cfg_file, force_run=True)
            if f"crashkernel={crashkernel}" not in result.stdout:
                raise LisaException(
                    f'No find "crasherkel={crashkernel}" in {cfg_file} after'
                    "insert. Please double check the grub config file and insert"
                    "process"
                )

        # Update grub
        update_cmd = self._get_crashkernel_update_cmd(crashkernel)
        result = self.node.execute(update_cmd, sudo=True)
        result.assert_exit_code(message="Failed to update grub")

    def config_dump_path(self) -> None:
        """
        If the system memory size is bigger than 1T, the default size of /var/crash
        maybe not enough to store the dump file, need change the dump path. The distro
        which doesn't have enough space for /var/crash, need override this method.
        """
        return

    def enable_kdump_service(self) -> None:
        """
        This method enable the kdump service. If distro has a different kdump service
        name, need override it.
        """
        service = self.node.tools[Service]
        service.enable_service("kdump")

    def set_unknown_nmi_panic(self) -> None:
        """
        /proc/sys/kernel/unknown_nmi_panic:
        The value in this file affects behavior of handling NMI. When the value is
        non-zero, unknown NMI is trapped and then panic occurs. If need to dump the
        crash, the value should be set 1. Some architectures don't provide architected
        NMIs,such as ARM64, the system doesn't have this file, we don't need to set
        either.
        """
        nmi_panic_file = PurePath("/proc/sys/kernel/unknown_nmi_panic")
        if self.node.shell.exists(nmi_panic_file):
            sysctl = self.node.tools[Sysctl]
            sysctl.write("kernel.unknown_nmi_panic", "1")

    @retry(exceptions=LisaException, tries=60, delay=1)  # type: ignore
    def _check_kexec_crash_loaded(self) -> None:
        """
        Sometimes it costs a while to load the value, so define this methed as @retry
        """
        cat = self.node.tools[Cat]
        result = cat.run(self.kexec_crash, force_run=True)
        if "1" != result.stdout:
            raise LisaException(f"{self.kexec_crash} file's value is not 1.")

    def _check_crashkernel_in_cmdline(self, crashkernel_memory: str) -> None:
        cat = self.node.tools[Cat]
        result = cat.run("/proc/cmdline", force_run=True)
        if f"crashkernel={crashkernel_memory}" not in result.stdout:
            raise LisaException(
                f"crashkernel={crashkernel_memory} boot parameter is not present in"
                "kernel cmdline"
            )

    def _check_crashkernel_memory_reserved(self) -> None:
        cat = self.node.tools[Cat]
        result = cat.run(self.iomem, force_run=True)
        if "Crash kernel" not in result.stdout:
            raise LisaException(
                f"No find 'Crash kernel' in {self.iomem}. Memory isn't reserved for"
                "crash kernel"
            )

    def check_crashkernel_loaded(self, crashkernel_memory: str) -> None:
        # Check crashkernel parameter in cmdline
        self._check_crashkernel_in_cmdline(crashkernel_memory)

        # Check crash kernel loaded
        if not self.node.shell.exists(PurePosixPath(self.kexec_crash)):
            raise LisaException(
                f"{self.kexec_crash} file doesn't exist. Kexec crash is not loaded."
            )
        self._check_kexec_crash_loaded()

        # Check if memory is reserved for crash kernel
        self._check_crashkernel_memory_reserved()

    def check_vmcore_exist(self) -> None:
        cmd = f"find {self.dump_path} -type f -size +10M"
        result = self.node.execute(cmd, shell=True, sudo=True)
        if result.stdout == "":
            raise LisaException(
                "No file was found in /var/crash of size greater than 10M."
                "The dump file didn't generate, please double check."
            )


class KdumpRedhat(KdumpBase):
    @property
    def command(self) -> str:
        return "kdumpctl"

    def _install(self) -> bool:
        assert isinstance(self.node.os, Redhat)
        self.node.os.install_packages("kexec-tools")
        return self._check_exists()

    def _get_crashkernel_cfg_file(self) -> str:
        if self.node.os.information.version >= "8.0.0-0":
            # For Redhat 8 and later version, we can use grubby command to config
            # crashkernel. No need to get the crashkernel cfg file
            return ""
        else:
            return "/etc/default/grub"

    def _get_crashkernel_update_cmd(self, crashkernel: str) -> str:
        if self.node.os.information.version >= "8.0.0-0":
            return f'grubby --update-kernel=ALL --args="crashkernel={crashkernel}"'
        else:
            if self.node.shell.exists(PurePosixPath("/sys/firmware/efi")):
                # System with UEFI firmware
                return "grub2-mkconfig -o /boot/efi/EFI/redhat/grub.cfg"
            else:
                # System with BIOS firmware
                return "grub2-mkconfig -o /boot/grub2/grub.cfg"

    def config_dump_path(self) -> None:
        """
        If the system memory size is bigger than 1T, the default size of /var/crash
        maybe not enough to store the dump file, need change the dump path
        """
        kdump_conf = "/etc/kdump.conf"
        check_memory_cmd = "free -h | grep Mem | awk '{print $2}'"
        result = self.node.execute(check_memory_cmd, shell=True, sudo=True)
        if "Ti" not in result.stdout:
            # System memeory size is smaller than 1T, no need to change dump path
            return
        size = float(result.stdout.strip("Ti"))
        if size > 1:
            self.dump_path = "/mnt/crash"
            self.node.execute(
                f"mkdir -p {self.dump_path}", shell=True, sudo=True
            ).assert_exit_code()
            # Change dump path in kdump conf
            sed = self.node.tools[Sed]
            sed.substitute(
                match_lines="^path",
                regexp="path",
                replacement="#path",
                file=kdump_conf,
                sudo=True,
            )
            sed.append(f"path {self.dump_path}", kdump_conf, sudo=True)


class KdumpDebian(KdumpBase):
    @property
    def command(self) -> str:
        return "kdump-config"

    def _install(self) -> bool:
        assert isinstance(self.node.os, Debian)
        self.node.os.install_packages("kdump-tools")
        return self._check_exists()

    def _get_crashkernel_cfg_file(self) -> str:
        return "/etc/default/grub.d/kdump-tools.cfg"

    def _get_crashkernel_update_cmd(self, crashkernel: str) -> str:
        return "update-grub"

    def enable_kdump_service(self) -> None:
        service = self.node.tools[Service]
        service.enable_service("kdump-tools")


class KdumpSuse(KdumpBase):
    @property
    def command(self) -> str:
        return "kdumptool"

    def _install(self) -> bool:
        assert isinstance(self.node.os, Suse)
        self.node.os.install_packages("kdump")
        return self._check_exists()
