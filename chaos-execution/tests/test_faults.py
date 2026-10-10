"""Every supported fault type maps to the right Toxiproxy call, and cleanup undoes it."""
import pytest

from chaos.config import load_safety
from chaos.toxiproxy import TOXICS, Toxiproxy, toxic_name

PROXY = "podinfo-backend"


@pytest.mark.parametrize("fault,intensity,attr", [
    ("latency", 300, "latency"), ("timeout", 800, "timeout"), ("reset_peer", 200, "timeout"),
    ("limit_data", 64, "bytes"), ("bandwidth", 10, "rate")])
def test_toxic_faults_use_the_right_type_and_attribute(env, fault, intensity, attr):
    state, _ = env
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_fault(PROXY, fault, toxic_name("x"), intensity)
    (t,) = state["toxics"]
    assert t["type"] == TOXICS[fault][0] and t["attributes"][attr] == intensity
    assert t["name"] == "chaos-x" and t["stream"] == "downstream"
    tp.remove_fault(PROXY, fault, toxic_name("x"))
    assert tp.state(PROXY) == {"enabled": True, "toxics": []}


def test_connection_disabled_switches_the_proxy_off_and_back_on(env):
    state, _ = env
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_fault(PROXY, "connection-disabled", toxic_name("x"), 1)
    assert state["enabled"] is False and state["toxics"] == []
    assert not tp.is_clean(PROXY, toxic_name("x"))            # a disabled proxy is NOT clean
    assert tp.state(PROXY)["enabled"] is False
    tp.remove_fault(PROXY, "connection-disabled", toxic_name("x"))
    assert tp.is_clean(PROXY, toxic_name("x"))


def test_state_reports_toxics_of_any_kind_not_only_ours(env):
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_toxic(PROXY, "someone-elses", "latency", {"latency": 5})
    assert tp.state(PROXY)["toxics"] == ["someone-elses"]
