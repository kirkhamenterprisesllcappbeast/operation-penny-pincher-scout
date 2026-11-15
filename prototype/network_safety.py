"""Bounded public DNS and TLS-pinned network connections."""
import socket
import http.client
import ssl
from ipaddress import ip_address

def public_dns(host: str) -> list[str]:
    """Resolve and retain only globally routable addresses; reject mixed results."""
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = sorted({item[4][0] for item in addresses})
    if not ips or any(not ip_address(ip).is_global for ip in ips):
        raise ValueError("DNS returned a non-public or empty address set")
    return ips


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the vetted IP while preserving hostname for TLS verification."""
    def __init__(self, host: str, pinned_ip: str, timeout: int):
        super().__init__(host, port=443, timeout=timeout,
                         context=ssl.create_default_context())
        self.pinned_ip = pinned_ip

    def connect(self):
        raw = socket.create_connection((self.pinned_ip, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise
