"""Workbench path containment and image-only pinned public HTTP transport."""
from __future__ import annotations

import http.client
import ipaddress
from pathlib import Path, PureWindowsPath
import socket
import ssl
from urllib.parse import unquote, urljoin, urlsplit


class SecurityError(ValueError):
    pass


def contained(root: Path, value: str | Path, *, base: Path | None = None) -> Path:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise SecurityError('WORKSPACE_PATH_BLOCKED')
    candidate = Path(value)
    # Relative data paths may not carry a drive, UNC or traversal on any OS.
    if isinstance(value, str):
        decoded = unquote(value)
        windows = PureWindowsPath(decoded)
        if windows.drive or windows.root or ':' in decoded or '\\' in decoded or '\x00' in decoded:
            raise SecurityError('WORKSPACE_PATH_BLOCKED')
        candidate = Path(decoded)
        if candidate.is_absolute() or '..' in candidate.parts:
            raise SecurityError('WORKSPACE_PATH_BLOCKED')
    candidate = candidate if candidate.is_absolute() else (base or root) / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise SecurityError('WORKSPACE_PATH_BLOCKED')
    return resolved


def public_target(url: str, resolver=socket.getaddrinfo) -> tuple[str, str, int, str, list[str]]:
    """Every resolved IP must be public; actual connection uses only these IPs."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').rstrip('.').lower().encode('idna').decode('ascii')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if (parts.scheme not in ('http', 'https') or not host or parts.username or parts.password
                or port != (443 if parts.scheme == 'https' else 80)
                or any(c.isspace() or ord(c) < 32 for c in url)
                or host == 'localhost' or host.endswith(('.localhost','.local','.internal','.lan'))):
            raise SecurityError('REMOTE_IMAGE_TARGET_BLOCKED')
        answers = resolver(host, port, type=socket.SOCK_STREAM)
        ips = sorted({str(ipaddress.ip_address(x[4][0])) for x in answers})
        if not ips:
            raise SecurityError('REMOTE_IMAGE_TARGET_BLOCKED')
        for value in ips:
            ip = ipaddress.ip_address(value)
            effective = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
            if not effective.is_global or effective.is_multicast:
                raise SecurityError('REMOTE_IMAGE_TARGET_BLOCKED')
        return parts.scheme, host, port, (parts.path or '/') + ('?' + parts.query if parts.query else ''), ips
    except (ValueError, UnicodeError, OSError) as error:
        raise SecurityError('REMOTE_IMAGE_TARGET_BLOCKED') from None


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, tls: bool):
        super().__init__(host, port=port, timeout=30)
        self.ip, self.tls = ip, tls

    def connect(self):
        # No second DNS lookup, requests proxy, or auto-follow redirect.
        sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)
        try:
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
        except Exception:
            sock.close()
            raise


def fetch_image(url: str, maximum: int, *, resolver=socket.getaddrinfo, connect= PinnedHTTP) -> bytes:
    """Bounded image GET; revalidate and pin DNS on each redirect."""
    if url.startswith('//'):
        url = 'https:' + url
    for _ in range(6):
        scheme, host, port, path, ips = public_target(url, resolver)
        conn = connect(host, port, ips[0], scheme == 'https')
        try:
            conn.request('GET', path, headers={'Accept': 'image/*', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301,302,303,307,308):
                location = response.getheader('Location')
                if not location:
                    raise SecurityError('REMOTE_IMAGE_FETCH_BLOCKED')
                url = urljoin(url, location)
                continue
            if response.status != 200 or not response.getheader('Content-Type','').lower().startswith('image/'):
                raise SecurityError('REMOTE_IMAGE_FETCH_BLOCKED')
            raw = response.read(maximum + 1)
            if not raw or len(raw) > maximum:
                raise SecurityError('REMOTE_IMAGE_FETCH_BLOCKED')
            return raw
        except (OSError, http.client.HTTPException):
            raise SecurityError('REMOTE_IMAGE_FETCH_BLOCKED') from None
        finally:
            conn.close()
    raise SecurityError('REMOTE_IMAGE_REDIRECT_LIMIT')
