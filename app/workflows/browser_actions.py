"""Owned Chromium sessions with a closed, exact-host network policy.

Production URLs are HTTPS on port 443 and public IP addresses only. DNS answers
are validated and pinned to Chromium host resolver rules, preventing a second
lookup from rebinding to a private service. Every request and redirect is also
checked. Browser state, screenshots, cookies and downloads are never persisted.
"""

import ipaddress
import os
import re
import select
import socket
import socketserver
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit

from app.workflows.registry import WorkflowFailure

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HOST = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


def _parse_url(url):
    try:
        if not isinstance(url, str) or len(url) > 4096 or "\\" in url:
            raise ValueError
        if any(ord(character) <= 32 for character in url):
            raise ValueError
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            raise ValueError
        _ = parsed.port
        return parsed
    except (ValueError, TypeError):
        raise WorkflowFailure("FLOW_BROWSER_URL_INVALID") from None


@dataclass(frozen=True)
class BrowserPolicy:
    """Construct in reviewed Python code; never deserialize policy from requests."""

    hosts: tuple[str, ...] = ()
    test_origin: str | None = None

    @classmethod
    def from_env(cls):
        raw = os.environ.get("RPA_BROWSER_ALLOWED_HOSTS", "")
        if not raw:
            return cls()
        hosts = tuple(dict.fromkeys(part.strip().lower() for part in raw.split(",")))
        if len(hosts) > 12 or any(
            not _HOST.fullmatch(host) or host.endswith(".") for host in hosts
        ):
            raise WorkflowFailure("FLOW_BROWSER_POLICY_INVALID")
        return cls(hosts=hosts)

    @classmethod
    def for_test_origin(cls, origin):
        """Permit only an explicit ephemeral loopback fixture, without env bypass."""
        parsed = _parse_url(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.port < 1024
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise WorkflowFailure("FLOW_BROWSER_TEST_ORIGIN_INVALID")
        return cls(test_origin=f"{parsed.scheme}://127.0.0.1:{parsed.port}")

    def validate(self, url):
        parsed = _parse_url(url)
        if self.test_origin:
            origin = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            if origin == self.test_origin:
                return parsed
            raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED")
        if not self.hosts:
            raise WorkflowFailure("FLOW_BROWSER_NOT_CONFIGURED")
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.hostname not in self.hosts
        ):
            raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED")
        return parsed

    def destinations(self):
        if self.test_origin:
            parsed = _parse_url(self.test_origin)
            return {("127.0.0.1", parsed.port): "127.0.0.1"}
        destinations = {}
        for host in self.hosts:
            if not isinstance(host, str) or not _HOST.fullmatch(host) or host.endswith("."):
                raise WorkflowFailure("FLOW_BROWSER_POLICY_INVALID")
            try:
                addresses = {
                    row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                }
            except OSError:
                raise WorkflowFailure("FLOW_BROWSER_DNS_UNAVAILABLE") from None
            if not addresses:
                raise WorkflowFailure("FLOW_BROWSER_DNS_UNAVAILABLE")
            try:
                parsed = [ipaddress.ip_address(address) for address in addresses]
            except ValueError:
                raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED") from None
            if any(not address.is_global or address.is_multicast for address in parsed):
                raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED")
            # Prefer IPv4 when available; brackets are required for IPv6 MAP.
            address = min(parsed, key=lambda item: (item.version, int(item)))
            destinations[(host, 443)] = str(address)
        return destinations

    def resolver_rules(self):
        if self.test_origin:
            return "MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"
        rules = []
        for (host, _port), address in self.destinations().items():
            destination = address if ":" not in address else f"[{address}]"
            rules.append(f"MAP {host} {destination}")
        return ", ".join([*rules, "MAP * ~NOTFOUND"])


class _BrowserProxy:
    """Owned CONNECT proxy pins every TLS destination, including redirects.

    Playwright's route handler only sees the initial URL of a redirected request.
    Chromium therefore has no direct network path: even redirected IP literals
    must establish an allowed tunnel through this proxy. The proxy never resolves
    an untrusted request's host and never records any request data.
    """

    def __init__(self, destinations, check_active, failed):
        self._sockets = set()
        self._lock = Lock()
        self._closed = False
        outer = self

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = False
            daemon_threads = True

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                remote = None
                try:
                    self.request.settimeout(5)
                    with outer._lock:
                        if outer._closed:
                            return
                        outer._sockets.add(self.request)
                    header = bytearray()
                    while b"\r\n\r\n" not in header and len(header) <= 8192:
                        part = self.request.recv(1)
                        if not part:
                            return
                        header.extend(part)
                    if len(header) > 8192:
                        raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED")
                    method, authority, protocol = (
                        bytes(header).split(b"\r\n", 1)[0].decode("ascii").split(" ")
                    )
                    if method != "CONNECT" or protocol not in {"HTTP/1.0", "HTTP/1.1"}:
                        raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED")
                    parsed = _parse_url("https://" + authority)
                    address = destinations.get((parsed.hostname, parsed.port or 443))
                    if address is None or parsed.path or parsed.query or parsed.fragment:
                        raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED")
                    check_active()
                    remote = socket.create_connection((address, parsed.port or 443), timeout=5)
                    with outer._lock:
                        if outer._closed:
                            return
                        outer._sockets.add(remote)
                    self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    sockets = [self.request, remote]
                    last_check = time.monotonic()
                    while not outer._closed:
                        readable, _, _ = select.select(sockets, [], [], 0.2)
                        if time.monotonic() - last_check >= 0.5:
                            check_active()
                            last_check = time.monotonic()
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            target = remote if source is self.request else self.request
                            target.sendall(data)
                except Exception as error:
                    # Broken client tunnels are ordinary cleanup; policy and
                    # ownership errors must reach the workflow state machine.
                    if not isinstance(error, OSError):
                        failed(error)
                    try:
                        self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    except OSError:
                        pass
                finally:
                    with outer._lock:
                        outer._sockets.discard(self.request)
                        outer._sockets.discard(remote)
                    if remote is not None:
                        remote.close()

        self._server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        with self._lock:
            self._closed = True
            sockets = list(self._sockets)
        for item in sockets:
            try:
                item.shutdown(socket.SHUT_RDWR)
                item.close()
            except OSError:
                pass
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


class BrowserSession:
    """One in-memory context per transaction, always closed by execute_graph."""

    def __init__(self, policy=None):
        self.policy = BrowserPolicy.from_env() if policy is None else policy
        if not isinstance(self.policy, BrowserPolicy):
            raise WorkflowFailure("FLOW_BROWSER_POLICY_INVALID")
        self.opened = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._blocked = False
        self._check_active = None
        self._route_failure = None
        self._proxy = None

    def validate_url(self, url):
        return self.policy.validate(url)

    def _launch(self):
        if not self.policy.test_origin and not self.policy.hosts:
            raise WorkflowFailure("FLOW_BROWSER_NOT_CONFIGURED")
        destinations = self.policy.destinations()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise WorkflowFailure("FLOW_BROWSER_NOT_INSTALLED") from None
        # Playwright's driver chooses the executable using the caller's env.
        # Supplying an explicit path avoids mutating global os.environ while
        # several workers are launching concurrently.
        executable = None
        bundled = sorted(
            (_PROJECT_ROOT / ".browser-cache").glob(
                "chromium_headless_shell-*/chrome-linux/headless_shell"
            )
        ) or sorted(
            (_PROJECT_ROOT / ".browser-cache").glob(
                "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
            )
        )
        if bundled:
            executable = str(bundled[-1])
        launch_env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TMPDIR"}
        }
        libs = _PROJECT_ROOT / ".browser-cache/system/usr/lib/x86_64-linux-gnu"
        if libs.is_dir():
            launch_env["LD_LIBRARY_PATH"] = str(libs)
        try:
            self._proxy = _BrowserProxy(destinations, self._active, self._failed)
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                executable_path=executable,
                env=launch_env,
                timeout=15000,
                proxy={"server": self._proxy.url, "bypass": "<-loopback>"},
                args=[
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                    "--disable-background-networking",
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-features=WebRtcHideLocalIpsWithMdns",
                ],
            )
            self._context = self._browser.new_context(
                accept_downloads=False,
                service_workers="block",
                ignore_https_errors=self.policy.test_origin is not None,
                locale="tr-TR",
                viewport={"width": 1280, "height": 900},
            )
            self._context.route("**/*", self._route)
            # WebSocket handshakes are not ordinary HTTP route callbacks.
            self._context.route_web_socket("**/*", lambda websocket: websocket.close())
            self._context.on("page", self._restrict_page)
            self._page = self._context.new_page()
            self._page.on("dialog", lambda dialog: dialog.dismiss())
            self._page.on("download", lambda download: download.cancel())
        except Exception:
            self.close()
            raise WorkflowFailure("FLOW_BROWSER_START_FAILED") from None

    def _restrict_page(self, page):
        if self._page is not None and page != self._page:
            page.close()

    def _active(self):
        if self._check_active is not None:
            self._check_active()

    def _failed(self, error):
        self._blocked = True
        self._route_failure = error

    def prepare(self):
        if self._browser is None:
            self._launch()

    def _route(self, route):
        try:
            if self._check_active is not None:
                self._check_active()
            self.policy.validate(route.request.url)
        except Exception as error:
            self._blocked = True
            self._route_failure = error
            route.abort()
            return
        route.continue_()

    def perform(self, kind, config, *, check_active, deadline):
        self._check_active = check_active
        check_active()
        if self._browser is None:
            self._launch()
        check_active()
        remaining_ms = min(10000, int((deadline - time.monotonic()) * 1000))
        if remaining_ms <= 0:
            raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
        self._page.set_default_timeout(remaining_ms)
        try:
            if kind == "browser_open":
                self.validate_url(config["url"])
                response = self._page.goto(
                    config["url"], wait_until="domcontentloaded", timeout=remaining_ms
                )
                if response is not None and response.status >= 400:
                    raise WorkflowFailure("FLOW_BROWSER_HTTP_FAILED")
                self.opened = True
                result = None
            else:
                if not self.opened:
                    raise WorkflowFailure("FLOW_BROWSER_PAGE_REQUIRED")
                locator = self._page.locator("css=" + config["selector"])
                if kind == "browser_fill":
                    locator.fill(config["value"])
                    result = None
                elif kind == "browser_click":
                    locator.click()
                    result = None
                elif kind == "browser_extract":
                    result = locator.inner_text()
                    if len(result.encode("utf-8")) > 8192:
                        raise WorkflowFailure("FLOW_BROWSER_RESULT_TOO_LARGE")
                else:
                    raise WorkflowFailure("FLOW_BROWSER_ACTION_INVALID")
            if self._route_failure is not None:
                raise self._route_failure
            if self._blocked:
                raise WorkflowFailure("FLOW_BROWSER_HOST_BLOCKED")
            check_active()
            return result
        except Exception:
            # Preserve lease/DB exceptions captured by a routed request.
            if self._route_failure is not None:
                raise self._route_failure from None
            raise

    def close(self):
        for resource in (self._context, self._browser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None
        self._context = self._browser = self._playwright = self._page = None
