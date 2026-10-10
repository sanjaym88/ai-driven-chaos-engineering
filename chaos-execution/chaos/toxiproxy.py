"""Thin wrapper over the Toxiproxy management API. Every fault we add is named after its run ID."""
import requests

PREFIX = "chaos-"
DISABLE = "connection-disabled"      # not a toxic: the proxy itself is switched off

# fault_type -> (toxic type, attribute name that carries the intensity)
TOXICS = {
    "latency": ("latency", "latency"),
    "timeout": ("timeout", "timeout"),
    "reset_peer": ("reset_peer", "timeout"),
    "limit_data": ("limit_data", "bytes"),
    "bandwidth": ("bandwidth", "rate"),
}


def toxic_name(run_id: str) -> str:
    return f"{PREFIX}{run_id}"


class Toxiproxy:
    def __init__(self, url: str, timeout: float = 3.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _req(self, method, path, **kw):
        return requests.request(method, self.url + path, timeout=self.timeout, **kw)

    def proxy(self, name: str) -> dict:
        r = self._req("GET", f"/proxies/{name}")
        r.raise_for_status()
        return r.json()

    def toxics(self, proxy: str) -> list:
        r = self._req("GET", f"/proxies/{proxy}/toxics")
        r.raise_for_status()
        return r.json()

    def state(self, proxy: str) -> dict:
        """Everything that must be 'clean' between cases: proxy enabled and ZERO toxics of any kind."""
        enabled = bool(self.proxy(proxy).get("enabled", False))
        return {"enabled": enabled, "toxics": [t["name"] for t in self.toxics(proxy)]}

    def set_enabled(self, proxy: str, enabled: bool) -> None:
        r = self._req("POST", f"/proxies/{proxy}", json={"enabled": enabled})
        r.raise_for_status()

    def add_toxic(self, proxy: str, name: str, toxic_type: str, attributes: dict) -> None:
        body = {"name": name, "type": toxic_type, "stream": "downstream", "toxicity": 1.0, "attributes": attributes}
        r = self._req("POST", f"/proxies/{proxy}/toxics", json=body)
        r.raise_for_status()

    def add_latency(self, proxy: str, name: str, latency_ms: int, jitter_ms: int = 0) -> None:
        self.add_toxic(proxy, name, "latency", {"latency": int(latency_ms), "jitter": int(jitter_ms)})

    def add_fault(self, proxy: str, fault_type: str, name: str, intensity: float) -> None:
        if fault_type == DISABLE:
            self.set_enabled(proxy, False)
            return
        toxic_type, attr = TOXICS[fault_type]
        attributes = {attr: int(intensity)}
        if fault_type == "latency":
            attributes["jitter"] = 0
        self.add_toxic(proxy, name, toxic_type, attributes)

    def remove_toxic(self, proxy: str, name: str) -> bool:
        """Idempotent: a toxic that is already gone counts as removed."""
        r = self._req("DELETE", f"/proxies/{proxy}/toxics/{name}")
        if r.status_code == 404:
            return True
        r.raise_for_status()
        return True

    def remove_fault(self, proxy: str, fault_type: str, name: str) -> bool:
        if fault_type == DISABLE:
            self.set_enabled(proxy, True)
            return True
        return self.remove_toxic(proxy, name)

    def chaos_toxics(self, proxy: str) -> list:
        return [t["name"] for t in self.toxics(proxy) if t["name"].startswith(PREFIX)]

    def is_clean(self, proxy: str, name: str | None = None) -> bool:
        """Proxy enabled and no (this run's | any chaos-) toxic left."""
        if not self.proxy(proxy).get("enabled", False):
            return False
        left = self.chaos_toxics(proxy)
        return (name not in left) if name else not left
