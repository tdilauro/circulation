from __future__ import annotations

from contextlib import contextmanager
import os

import paramiko
from sshtunnel import SSHTunnelForwarder

SSH_CONFIG_FILE = os.path.expanduser("~/.ssh/config")
DEFAULT_SSH_KEYFILE = os.path.expanduser("~/.ssh/id_rsa")
DEFAULT_SSH_PORT = 922


class SSHTunnelIOError(IOError):
    """IOError setting up SSH tunnel."""


def ssh_config_for_host(host, config_file=SSH_CONFIG_FILE) -> dict:
    ssh_config_parser = paramiko.config.SSHConfig()
    ssh_config_file = os.path.expanduser(config_file)

    try:
        with open(ssh_config_file) as f:
            ssh_config_parser.parse(f)
    except IOError as e:
        raise SSHTunnelIOError(
            f"Error accessing ssh config file {ssh_config_file}. Code: {e.errno} Reason {e.strerror}"
        ) from e

    if not (ssh_config := ssh_config_parser.lookup(host)):
        return {}

    ssh_config_info = {key: ssh_config[key] for key in ('hostname', 'user', 'port') if key in ssh_config}

    if 'identityfile' in ssh_config:
        key_file = ssh_config['identityfile']
        if isinstance(key_file, list):
            key_file = key_file[0]
        ssh_config_info['identityfile'] = key_file

    return ssh_config_info


class SimpleSSHTunnel:

    def __init__(
            self,
            *,
            destination_host: str,
            destination_port: int,
            bypass=False,
            ssh_user: str,
            ssh_host: str,
            ssh_port=DEFAULT_SSH_PORT,
    ) -> None:
        """Create an SSH tunnel to the destination.

        If `bypass` is True, then skip creating the tunnel.

        :param destination_host: The ultimate destination host.
        :param destination_port: The port on the destination host.
        :param bypass: Whether to skip creating the tunnel.
        :param ssh_user: The SSH user.
        :param ssh_host: The SSH through which we'll connect.
        :param ssh_port:
        """

        if bypass:
            self._tunnel = None
            self.local_bind_host = destination_host
            self.local_bind_port = destination_port
        else:
            _ssh_config = ssh_config_for_host(ssh_host)
            _identityfile = _ssh_config.get("identityfile")
            self._tunnel = SSHTunnelForwarder(
                ssh_address_or_host=(ssh_host, ssh_port),
                ssh_config_file=SSH_CONFIG_FILE,
                ssh_username=ssh_user,
                allow_agent=True,
                ssh_private_key=_identityfile,
                host_pkey_directories=[os.path.expanduser("~/.ssh")],
                remote_bind_address=(destination_host, destination_port),
            )
            self.local_bind_host = None
            self.local_bind_port = None

    def __enter__(self) -> SimpleSSHTunnel:
        if self._tunnel:
            self._tunnel.start()
            self.local_bind_host = self._tunnel.local_bind_host
            self.local_bind_port = self._tunnel.local_bind_port

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._tunnel:
            self._tunnel.stop()


@contextmanager
def tunnel_for(
    ssh_host: str, ssh_user: str,
    target_host: str, target_port: int,
    ssh_port=DEFAULT_SSH_PORT,
    use_tunnel=True
) -> SSHTunnelForwarder:
    """
    Create an SSH tunnel using an SSH agent to ensure no password prompts occur.

    :param ssh_host: The SSH host to connect through.
    :param ssh_user: The SSH user.
    :param target_host: The destination host for the connection.
    :param target_port: The destination port for the connection.
    :param ssh_port: The port to use for SSH.
    :param use_tunnel: Whether to create a tunnel or not.
    """

    # If we're not creating a tunnel, yield None.
    if not use_tunnel:
        yield None
        return

    ssh_config = ssh_config_for_host(ssh_host)
    identityfile = ssh_config.get("identityfile")

    # Use keys from ssh-agent directly to avoid passphrase prompt issues
    loaded_keys = ssh_pkey=paramiko.agent.Agent().get_keys(),

    if not loaded_keys:
        raise SSHTunnelIOError("No keys are loaded in ssh-agent. Please ensure the agent is running and keys are added.")

    # Configure the SSH tunnel
    ssh_tunnel = SSHTunnelForwarder(
        ssh_address_or_host=(ssh_host, ssh_port),
        ssh_config_file=SSH_CONFIG_FILE,
        ssh_username=ssh_user,
        ssh_pkeys=loaded_keys,  # Use keys from ssh-agent
        allow_agent=True,
        remote_bind_address=(target_host, target_port),
    )

    try:
        ssh_tunnel.start()
        yield ssh_tunnel
    finally:
        ssh_tunnel.stop()
