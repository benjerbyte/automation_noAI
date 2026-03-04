"""
Device session: connect using only host (IP or hostname) and credentials.
No static inventory; credentials are never stored.
Platform can be selected or auto-detected.
"""
import datetime
from pathlib import Path
from typing import Optional, Tuple, Union

from netmiko import ConnectHandler
from netmiko.ssh_autodetect import SSHDetect

# Backup command per platform (no secrets, just CLI command name)
BACKUP_COMMANDS = {
    "cisco_ios": "show running-config",
    "cisco_ios_ssh": "show running-config",
    "cisco_nxos": "show running-config",
    "cisco_nxos_ssh": "show running-config",
    "arista_eos": "show running-config",
    "arista_eos_ssh": "show running-config",
    "paloalto_panos": "show config running",
    "checkpoint_gaia": "show configuration",
}

# Template subdirectory per platform: commands/syntax differ (e.g. IOS vs EOS SNMP).
# Use this to load the correct template: templates/<dir>/snmp.j2, base.j2, etc.
PLATFORM_TEMPLATE_DIR = {
    "cisco_ios": "ios",
    "cisco_ios_ssh": "ios",
    "cisco_nxos": "nxos",
    "cisco_nxos_ssh": "nxos",
    "arista_eos": "eos",
    "arista_eos_ssh": "eos",
    "paloalto_panos": "panos",
    "checkpoint_gaia": "gaia",
}


def _normalize_platform(platform: Optional[str]) -> Optional[str]:
    if not platform or not platform.strip():
        return None
    p = platform.strip().lower()
    if p == "ios":
        return "cisco_ios_ssh"
    if p == "nxos":
        return "cisco_nxos_ssh"
    if p == "eos":
        return "arista_eos_ssh"
    if p in ("panos", "paloalto", "palo_alto"):
        return "paloalto_panos"
    if p in ("checkpoint", "gaia", "check_point"):
        return "checkpoint_gaia"
    return p


class DeviceSession:
    """
    Connect to a single device using provided host and credentials.
    All parameters are used only in memory; nothing is persisted.
    Platform is optional: if not provided, Netmiko will auto-detect from the device.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        platform: Optional[str] = None,
        *,
        port: int = 22,
        enable_secret: Optional[str] = None,
    ):
        """
        host: IP address or hostname of the device.
        username: Login username.
        password: Login password.
        platform: Optional. One of cisco_ios_ssh, cisco_nxos_ssh, arista_eos_ssh,
                  paloalto_panos, checkpoint_gaia (or ios, nxos, eos, panos, gaia).
                  If None or empty, the device type is auto-detected.
        port: SSH port (default 22).
        enable_secret: Optional enable/privileged password.
        """
        self.host = host.strip()
        self.username = username
        self.password = password
        self.platform = _normalize_platform(platform)  # None means auto-detect
        self.port = port
        self.enable_secret = enable_secret
        self._connection = None
        self._detected_platform: Optional[str] = None  # set after auto-detect
        self._device_hostname: Optional[str] = None  # hostname from device prompt, cached

    def get_device_hostname(self) -> str:
        """Return the hostname reported by the device (from CLI prompt). Falls back to connection host if unavailable."""
        if self._device_hostname is not None:
            return self._device_hostname
        if self._connection is None:
            return self.host
        try:
            prompt = self._connection.find_prompt()
            # Strip trailing prompt chars (e.g. #, >, ) and whitespace
            hostname = prompt.rstrip().rstrip("#>)\"]").strip()
            self._device_hostname = hostname or self.host
            return self._device_hostname
        except Exception:
            return self.host

    def _connect(self):
        if self._connection is not None:
            return
        device_type = self.platform
        if not device_type:
            # Auto-detect: use SSHDetect then connect with detected type
            params = {
                "device_type": "autodetect",
                "host": self.host,
                "username": self.username,
                "password": self.password,
                "port": self.port,
            }
            if self.enable_secret:
                params["secret"] = self.enable_secret
            guesser = SSHDetect(**params)
            device_type = guesser.autodetect()
            if not device_type:
                raise RuntimeError(
                    "Could not auto-detect device type. Please select the platform manually."
                )
            self._detected_platform = device_type
        params = {
            "device_type": device_type,
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "port": self.port,
        }
        if self.enable_secret:
            params["secret"] = self.enable_secret
        self._connection = ConnectHandler(**params)
        if self._detected_platform and not self.platform:
            self.platform = self._detected_platform

    def connect(self):
        """
        Public entrypoint to establish the SSH connection without running any commands.
        """
        self._connect()

    def _effective_platform(self) -> str:
        """Platform to use for backup command lookup (detected or user-selected)."""
        return self.platform or self._detected_platform or "cisco_ios_ssh"

    def get_template_dir(self) -> str:
        """
        Template subdirectory for this session's platform (e.g. 'ios', 'eos', 'nxos').
        Use when rendering config: load from templates/<dir>/snmp.j2, base.j2, etc.,
        so the correct syntax is used (Cisco IOS vs Arista EOS vs NX-OS).
        """
        plat = self._effective_platform()
        return PLATFORM_TEMPLATE_DIR.get(plat, "ios")

    def disconnect(self):
        if self._connection is not None:
            try:
                self._connection.disconnect()
            except Exception:
                pass
            self._connection = None

    def view(self, command: str) -> str:
        """Run a show (or any) command and return output. Connection stays open until you call disconnect()."""
        self._connect()
        out = self._connection.send_command(
            command.strip(), delay_factor=2, read_timeout=60
        )
        return out or ""

    def backup_configuration(self, save_dir: Union[str, Path]) -> Tuple[str, str]:
        """
        Retrieve running config and save to save_dir. Connection stays open until you call disconnect().
        Returns (absolute_path, message).
        """
        self._connect()
        cmd = BACKUP_COMMANDS.get(self._effective_platform()) or BACKUP_COMMANDS.get(
            "cisco_ios_ssh"
        )
        config = self._connection.send_command(
            cmd, delay_factor=2, read_timeout=90
        )
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.host.replace(".", "_").replace(":", "_")
        date_str = datetime.date.today().isoformat()
        filename = f"{safe_name}_{date_str}.txt"
        path = save_dir / filename
        path.write_text(config or "", encoding="utf-8")
        return str(path.resolve()), (
            f"[INFO] Backup saved to {path}. Connection remains open until you disconnect."
        )

    def send_config(self, config_lines: list[str]) -> str:
        """
        Send a list of configuration lines (merge). Connection stays open until you call disconnect().
        config_lines: e.g. ["interface Gi0/1", "description uplink", "no shutdown"]
        """
        self._connect()
        if isinstance(config_lines, str):
            config_lines = [line.strip() for line in config_lines.splitlines() if line.strip()]
        out = self._connection.send_config_set(
            config_lines, delay_factor=2, exit_config_mode=True
        )
        return out or ""
