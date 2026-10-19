import httpx
import pytest

import hetzner_watch.acquire as acquire
from hetzner_watch.acquire import Api, Step, next_step, swap

SID, PH = 1, 2


@pytest.mark.parametrize(
    "vm_type, holding, available, expected",
    [
        ("cx43", True, False, Step.DONE),
        ("cx33", False, False, Step.WAIT),
        ("cx33", False, True, Step.RESERVE),
        # A held placeholder swaps whatever the catalog says: it is the capacity.
        ("cx33", True, False, Step.SWAP),
    ],
)
def test_next_step(vm_type, holding, available, expected):
    assert next_step(vm_type, "cx43", holding, available) is expected


class FakeHetzner:
    """Just enough of the API for swap(): every action finishes at once."""

    def __init__(self, resize_errors):
        self.resize_errors = list(resize_errors)  # one entry per change_type call, None = ok
        self.status = "running"
        self.deleted = []
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path.removeprefix("/v1"), request.method
        self.calls.append((method, path))
        ok = {"action": {"id": 9, "status": "running"}}
        if method == "GET" and path.startswith("/actions/"):
            return httpx.Response(200, json={"action": {"id": 9, "status": "success"}})
        if method == "GET" and path == f"/servers/{SID}":
            return httpx.Response(200, json={"server": {"status": self.status}})
        if path.endswith("/actions/shutdown"):
            self.status = "off"
            return httpx.Response(201, json=ok)
        if path.endswith("/actions/poweron"):
            self.status = "running"
            return httpx.Response(201, json=ok)
        if path.endswith("/actions/create_image"):
            return httpx.Response(201, json={**ok, "image": {"id": 77}})
        if path.endswith("/actions/change_type"):
            err = self.resize_errors.pop(0)
            if err:
                return httpx.Response(412, json={"error": {"code": err}})
            self.status = "running"  # Hetzner powers the server on after a resize
            return httpx.Response(201, json=ok)
        if method == "DELETE":
            self.deleted.append(int(path.rsplit("/", 1)[1]))
            return httpx.Response(200, json={"action": {"id": 9}})
        raise AssertionError(f"unexpected {method} {path}")


def run_swap(monkeypatch, fake):
    monkeypatch.setattr(acquire, "POLL_S", 0)
    return swap(Api("t", httpx.MockTransport(fake)), SID, "cx43", PH)


def test_a_second_free_slot_keeps_the_placeholder_until_the_resize_is_done(monkeypatch):
    fake = FakeHetzner([None])
    ok, _ = run_swap(monkeypatch, fake)
    assert ok and fake.deleted == [PH] and fake.status == "running"
    resize_at = fake.calls.index(("POST", f"/servers/{SID}/actions/change_type"))
    assert fake.calls.index(("DELETE", f"/servers/{PH}")) > resize_at


def test_no_capacity_releases_the_placeholder_and_retries_at_once(monkeypatch):
    fake = FakeHetzner(["resource_unavailable", None])
    ok, _ = run_swap(monkeypatch, fake)
    assert ok and fake.deleted == [PH]


def test_losing_the_slot_in_the_swap_powers_the_vm_back_on(monkeypatch):
    fake = FakeHetzner(["resource_unavailable", "resource_unavailable"])
    ok, _ = run_swap(monkeypatch, fake)
    assert not ok and fake.deleted == [PH] and fake.status == "running"


def test_any_other_resize_error_keeps_the_placeholder(monkeypatch):
    fake = FakeHetzner(["invalid_input"])
    with pytest.raises(RuntimeError, match="placeholder kept"):
        run_swap(monkeypatch, fake)
    assert fake.deleted == [] and fake.status == "running"
