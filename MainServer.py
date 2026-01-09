#!/usr/bin/env python3
import asyncio
import os
from pathlib import Path
from datetime import datetime

import asyncssh

# ====== Static config (as requested) ======
SERVER_IP = "130.49.146.15"
SERVER_PORT = 1234
USERNAME = "sftpuser"
PASSWORD = "sftppass123"
# =========================================

# Where to store uploads on the server machine:
SFTP_ROOT = Path("./uploads").resolve()

# If your machine does NOT own SERVER_IP, bind will fail.
# Keep SERVER_IP static above; you can change only this bind host for local testing.
BIND_HOST = SERVER_IP  # try "0.0.0.0" for local test if needed


def format_bytes(size):
    """Format bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


class MySFTPServer(asyncssh.SFTPServer):
    """Custom SFTP server that restricts access to SFTP_ROOT directory with logging."""
    
    def __init__(self, conn):
        # Set root directory before calling parent __init__
        # This way asyncssh will automatically restrict all operations to this root
        root = str(SFTP_ROOT)
        super().__init__(conn, chroot=root)
        self._active_uploads = {}
    
    def format_user(self, uid):
        return str(uid)
    
    def format_group(self, gid):
        return str(gid)
    
    async def open(self, path, pflags, attrs):
        """Override open to log file operations."""
        # Check if this is a write operation
        is_write = bool(pflags & asyncssh.FXF_WRITE)
        
        if is_write:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[server] [{timestamp}] Receiving file: {path}")
            self._active_uploads[path] = {
                'start_time': datetime.now(),
                'path': path
            }
        
        # Call parent open method
        result = await super().open(path, pflags, attrs)
        return result
    
    async def close(self, file_obj):
        """Override close to log completion."""
        # Get file path from the file object
        if hasattr(file_obj, '_filename'):
            path = file_obj._filename
            if path in self._active_uploads:
                upload_info = self._active_uploads.pop(path)
                elapsed = (datetime.now() - upload_info['start_time']).total_seconds()
                
                # Get file size
                try:
                    full_path = SFTP_ROOT / path.lstrip('/')
                    if full_path.exists():
                        size = full_path.stat().st_size
                        speed = size / elapsed if elapsed > 0 else 0
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        print(f"[server] [{timestamp}] ✓ Completed: {path} ({format_bytes(size)}) "
                              f"in {elapsed:.2f}s ({format_bytes(speed)}/s)")
                except Exception:
                    pass
        
        # Call parent close method
        return await super().close(file_obj)


class MySSHServer(asyncssh.SSHServer):
    """SSH server with password authentication."""
    
    def connection_made(self, conn):
        print(f"[server] Connection from {conn.get_extra_info('peername')}")
    
    def connection_lost(self, exc):
        if exc:
            print(f"[server] Connection error: {exc}")
    
    def password_auth_supported(self):
        return True
    
    def validate_password(self, username, password):
        if username == USERNAME and password == PASSWORD:
            return True
        return False


async def start_server():
    """Start the SFTP server."""
    # Ensure upload directory exists
    SFTP_ROOT.mkdir(parents=True, exist_ok=True)
    
    # Generate or load host keys
    host_key_path = Path("ssh_host_rsa_key")
    if not host_key_path.exists():
        print("[server] Generating host key...")
        key = asyncssh.generate_private_key('ssh-rsa', key_size=2048)
        host_key_path.write_text(key.export_private_key().decode())
    
    print(f"[server] SFTP ROOT: {SFTP_ROOT}")
    print(f"[server] Static config IP/PORT: {SERVER_IP}:{SERVER_PORT}")
    print(f"[server] Binding on: {BIND_HOST}:{SERVER_PORT}")
    print(f"[server] Credentials: {USERNAME} / {PASSWORD}")
    
    # Create server with custom SFTP handler
    await asyncssh.create_server(
        MySSHServer,
        BIND_HOST,
        SERVER_PORT,
        server_host_keys=[str(host_key_path)],
        sftp_factory=MySFTPServer,
    )
    
    print("[server] Server started, listening for connections...")


async def main():
    """Main server loop."""
    await start_server()
    
    # Keep server running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n[server] Shutting down...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[server] Server stopped")
