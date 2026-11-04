import importlib
import json
from pathlib import Path

import httpx
import pytest

from android_use.backend import UseError


@pytest.fixture
def benchmark(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("benchmark_executor")


def test_aggregate_includes_failed_run_cost_and_unknown_cost(benchmark):
    rows = [
        {
            "arm": "jev",
            "success": True,
            "elapsed_seconds": 2,
            "metrics": {"estimated_cost_usd": 0.01, "model_calls": 1},
        },
        {
            "arm": "jev",
            "success": False,
            "elapsed_seconds": 10,
            "metrics": {"estimated_cost_usd": 0.02, "model_calls": 2},
        },
    ]
    result = benchmark.aggregate(rows)["jev"]
    assert result["cost_per_success_usd"] == pytest.approx(0.03)
    assert result["median_seconds_all_runs"] == 6
    assert result["success_rate"] == 0.5 and result["model_calls"] == 3
    rows[1]["metrics"]["estimated_cost_usd"] = None
    assert benchmark.aggregate(rows)["jev"]["estimated_cost_usd"] is None


@pytest.fixture
def agent_benchmark(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("benchmark_agent")


def test_host_keeps_batch_and_verifies_actual_tree(agent_benchmark, rig, monkeypatch):
    import time

    runtime, backend = rig
    runtime._connect()
    package = agent_benchmark.PACKAGE
    backend.front["package"] = package
    backend.xml = f'<hierarchy><node package="{package}" text="Start" enabled="true" bounds="[0,100][200,300]"/></hierarchy>'
    monkeypatch.setattr(
        agent_benchmark,
        "ask",
        lambda *a: {
            "tool": "batch",
            "args": {"steps": [{"op": "tap", "args": {"x": 1, "y": 101}}]},
        },
    )
    original = backend.tap

    def tap(*args):
        original(*args)
        backend.xml = backend.xml.replace('text="Start"', 'text="Done"')

    backend.tap = tap
    result, decisions = agent_benchmark.run_host(
        runtime,
        {
            "goal": "Finish",
            "package": package,
            "completion": {"text": "Done"},
        },
        "test",
        time.monotonic() + 2,
    )
    assert result["verified"] and decisions == 1 and len(backend.actions) == 1


def test_host_verification_timeout_is_failed_not_lost(agent_benchmark, monkeypatch):
    def timeout(*args):
        raise UseError("host_timeout", "Timed out")

    monkeypatch.setattr(agent_benchmark, "ask", timeout)
    result = agent_benchmark.verify_host({}, 0)
    assert not result["verified"] and result["error"] == "host_timeout"


def test_host_wait_and_protocol_bytes_are_metered(agent_benchmark, monkeypatch):
    import io
    import sys

    meter = {
        "host_requests": 0,
        "host_replies": 0,
        "host_wait_seconds": 0.0,
        "host_input_utf8_bytes": 0,
        "host_output_utf8_bytes": 0,
    }
    monkeypatch.setattr(agent_benchmark, "METER", meter)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"choice":"a0"}\n'))
    monkeypatch.setattr(agent_benchmark.select, "select", lambda *a: ([sys.stdin], [], []))
    assert agent_benchmark.ask({"event": "choose"}, 1) == {"choice": "a0"}
    assert meter["host_requests"] == meter["host_replies"] == 1
    assert meter["host_input_utf8_bytes"] > 0 and meter["host_output_utf8_bytes"] > 0
    assert meter["host_wait_seconds"] >= 0


def test_hybrid_summary_never_counts_host_as_free(agent_benchmark):
    result = agent_benchmark.summarize(
        [
            {
                "arm": "agent_jev",
                "success": True,
                "elapsed_seconds": 1,
                "host_decisions": 2,
                "jev_calls": 1,
                "jev_estimated_cost_usd": 0.001,
            }
        ]
    )["agent_jev"]
    assert result["host_decisions"] == 2 and result["jev_calls"] == 1
    assert result["host_cost_usd"] is None and result["total_cost_usd"] is None


@pytest.mark.parametrize("choice,priced", [("a0", True), ("a0", False), ("invented", False)])
def test_generative_baseline_contract(benchmark, monkeypatch, choice, priced):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://baseline.example/v1")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test-baseline-secret")
    monkeypatch.setenv("TEXT_MODEL", "test-model")
    for name, value in [("INPUT", "1"), ("OUTPUT", "2")]:
        key = f"TEXT_MODEL_{name}_USD_PER_MILLION"
        if priced:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)

    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-baseline-secret"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                },
                "choices": [{"message": {"content": json.dumps({"choice": choice})}}],
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        benchmark.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )
    policy = benchmark.TextPolicy()
    if choice == "invented":
        with pytest.raises(UseError):
            policy.decide({"goal": "test"}, {"a0": "tap"}, 1)
    else:
        assert policy.decide({"goal": "test"}, {"a0": "tap"}, 1)["choice"] == "a0"
    assert len(calls) == 1
    if priced:
        assert policy.summary()["estimated_cost_usd"] == pytest.approx(0.00012)
    else:
        assert policy.summary()["estimated_cost_usd"] is None
    policy.close()
