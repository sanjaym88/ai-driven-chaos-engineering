from chaos.config import load_safety
from chaos.toxiproxy import Toxiproxy, toxic_name

PROXY = "podinfo-backend"


def test_add_remove_and_clean(env):
    state, _ = env
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_latency(PROXY, toxic_name("a"), 200)
    assert tp.chaos_toxics(PROXY) == ["chaos-a"]
    assert not tp.is_clean(PROXY, "chaos-a")
    assert tp.remove_toxic(PROXY, "chaos-a")
    assert tp.is_clean(PROXY, "chaos-a")


def test_remove_is_idempotent(env):
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    assert tp.remove_toxic(PROXY, "chaos-never-existed")


def test_clean_check_only_looks_at_own_toxic_when_named(env):
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_latency(PROXY, "chaos-other", 100)
    assert tp.is_clean(PROXY, "chaos-mine")
    assert not tp.is_clean(PROXY)
