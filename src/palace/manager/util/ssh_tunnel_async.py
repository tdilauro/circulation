from __future__ import annotations

from getpass import getpass

import asyncssh
import asyncio
import os
import socket
import paramiko

# Define a global constant for the default SSH port
SSH_DEFAULT_PORT = 22

class SSHTunnel:
    def __init__(
        self,
        *,
        ssh_host: str,
        target_host: str,
        target_port: int,
        ssh_port: int | None = None,
        local_host: str | None = None,
        local_port: int | None = None,
        ssh_user: str | None = None,
        config_file: str | None = None
    ):
        self.ssh_host = ssh_host
        self._ssh_port = ssh_port
        self.target_host = target_host
        self.target_port = target_port
        self.local_host = local_host  or "localhost"
        self.local_port = local_port or self._get_available_port()
        self.user = ssh_user
        self.config_file = config_file or os.path.expanduser("~/.ssh/config")
        self.connection: asyncssh.SSHClientConnection | None = None
        self.jump_conn: asyncssh.SSHClientConnection | None = None
        self.identity_files: list[str] = []

        # Load SSH config to potentially override some defaults.
        self._load_ssh_config()

        print(f"Tunnel: {self.__dict__=}")

    @property
    def ssh_port(self) -> int:
        return self._ssh_port or SSH_DEFAULT_PORT

    async def _establish_connection_with_fallback(self, host: str, port: int, username: str, client_keys: list[str], tunnel=None) -> asyncssh.SSHClientConnection:
        """
        Establish an SSH connection using fallback logic:
        1. Attempt to use the SSH agent keys first.
        2. If that fails and identity files are configured, try them one by one, optionally prompting for passphrases.
        """
        try:
            print(f"Attempting authentication using SSH agent for {host}:{port} as user {username}")
            return await asyncssh.connect(
                host,
                port=port,
                username=username,
                agent_forwarding=True,
                tunnel=tunnel,
            )
        except (asyncssh.PermissionDenied, asyncssh.KeyImportError):
            pass

        if not client_keys:
            raise asyncssh.PermissionDenied(f"SSH agent failed, and no identity files are configured for {host}.")

        for identity_file in client_keys:
            try:
                print(f"Attempting authentication using identity file: {identity_file}")
                passphrase = getpass(f"Enter passphrase for {identity_file}: ")
                return await asyncssh.connect(
                    host,
                    port=port,
                    username=username,
                    client_keys=[identity_file],
                    passphrase=passphrase,
                    tunnel=tunnel,
                )
            except asyncssh.PermissionDenied:
                print(f"Authentication failed for identity file: {identity_file}")
            except asyncssh.KeyImportError as exc:
                print(f"Could not import key {identity_file}. Error: {exc}")

        raise asyncssh.PermissionDenied(f"All authentication methods failed for {host}.")

    async def _connect(self) -> None:
        """
        Establish the SSH tunnel connection, considering ProxyJump if configured.
        """
        print(f"Attempting to connect to {self.ssh_host}:{self.ssh_port} as user {self.user}")

        jump_conn = None
        if self.jump_host_config:
            print(f"Using jumphost {self.jump_host_config['hostname']} to connect to {self.ssh_host}:{self.ssh_port}")
            jump_conn = await self._establish_connection_with_fallback(
                host=self.jump_host_config["hostname"],
                port=int(self.jump_host_config.get("port", SSH_DEFAULT_PORT)),
                username=self.jump_host_config.get("user", self.user),
                client_keys=self.jump_host_config.get("identityfile", self.identity_files),
            )

        self.connection = await self._establish_connection_with_fallback(
            host=self.ssh_host,
            port=self.ssh_port,
            username=self.user,
            client_keys=self.identity_files,
            tunnel=jump_conn,
        )
        await self._forward_connection()


    async def _forward_connection(self) -> None:
        """Forward the local port to the target host and target port."""
        try:
            print(f"Setting up port forwarding {self.local_host}:{self.local_port} -> {self.target_host}:{self.target_port}")
            await self.connection.forward_local_port(
                self.local_host, self.local_port, self.target_host, self.target_port
            )
            print("Port forwarding successful.")
        except Exception as e:
            print(f"Error in port forwarding: {e}")
            raise


    async def _disconnect(self) -> None:
        if self.connection:
            self.connection.close()
        if self.jump_conn:
            self.jump_conn.close()

    def _load_ssh_config(self) -> None:
        config = paramiko.SSHConfig()
        with open(self.config_file) as f:
            config.parse(f)

        host_config = config.lookup(self.ssh_host)
        print(f"SSH config: {host_config=}")
        if host_config:
            if self._ssh_port is None:
                port = host_config.get("port")
                self._ssh_port = int(port) if port else SSH_DEFAULT_PORT
            if self.user is None:
                self.user = host_config.get("user")
            if "identityfile" in host_config:
                identity_files = host_config["identityfile"]
                self.identity_files = [os.path.expanduser(file.strip()) for file in identity_files]
            if "proxyjump" in host_config:
                # Resolve ProxyJump to the actual configuration of the jump host
                self.jump_host_config = config.lookup(host_config["proxyjump"])
                print(f"Resolved jump host config: {self.jump_host_config}")


    @staticmethod
    def _get_available_port() -> int:
        """Find an available port on localhost."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def __enter__(self) -> SSHTunnel:
        """Synchronous entry point for the SSHTunnel.

        Initializes and connects the tunnel using an internal event loop.
        Raises a RuntimeError if an active event loop exists, as synchronous
        context managers cannot be used within an active async context.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Synchronous exit point for the SSHTunnel.
        Disconnects the tunnel and closes the internal event loop.
        """
        if hasattr(self, "_loop") and not self._loop.is_closed():
            self._loop.run_until_complete(self._disconnect())
            self._loop.close()



    async def __aenter__(self) -> SSHTunnel:
        await self._connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._disconnect()

# Example usage
if __name__ == "__main__":
    # Synchronous usage
    with SSHTunnel(ssh_host="example.com", target_host="target.com", target_port=80) as tunnel:
        print(f"Tunnel established from {tunnel.local_host}:{tunnel.local_port} to {tunnel.target_host}:{tunnel.target_port}")
        import time
        time.sleep(60)  # Keep the tunnel open for 60 seconds

    # Asynchronous usage
    async def async_usage():
        async with SSHTunnel(ssh_host="example.com", target_host="target.com", target_port=80) as tunnel:
            print(f"Tunnel established from {tunnel.local_host}:{tunnel.local_port} to {tunnel.target_host}:{tunnel.target_port}")
            await asyncio.sleep(60)  # Keep the tunnel open for
