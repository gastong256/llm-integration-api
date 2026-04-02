from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.observability.yml")
INFER_MODEL = "gpt-4o-mini"
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_CYAN = "\033[36m"
ANSI_BLUE = "\033[34m"
ANSI_YELLOW = "\033[33m"


@dataclass
class DemoConfig:
    base_url: str
    jaeger_url: str
    grafana_url: str
    api_key: str
    rate_limit_key: str
    auto: bool
    scene: int | None
    compose_files: tuple[str, ...]


class DemoRunner:
    def __init__(self, config: DemoConfig) -> None:
        self.config = config
        self.client = httpx.Client(timeout=35.0)
        self.demo_id = str(int(time.time()))
        self.scene_map = {
            0: ("llm-integration-api", self.scene_cover),
            1: ("Architecture", self.scene_context),
            2: ("Infer happy path", self.scene_infer_happy_path),
            3: ("Cache hit", self.scene_cache_hit),
            4: ("Classify through wrappers", self.scene_classify_wrappers),
            5: ("Rate limiting with headers", self.scene_rate_limit),
            6: ("Circuit breaker opening and recovery", self.scene_circuit_breaker),
            7: ("Redis degradation", self.scene_redis_degradation),
        }

    def run(self) -> None:
        scene_numbers = (
            [self.config.scene] if self.config.scene is not None else list(self.scene_map)
        )
        for number in scene_numbers:
            title, handler = self.scene_map[number]
            self._banner(number, title)
            handler()
            self._pause(self._next_hint(number))

    def scene_cover(self) -> None:
        print(f"{ANSI_CYAN}{ANSI_BOLD}LLM integration gateway demo{ANSI_RESET}")
        print(
            f"{ANSI_DIM}LLM proxy with Redis-backed caching, sliding-window rate "
            "limiting, a circuit breaker, and an embedded scikit-learn classifier."
            f"{ANSI_RESET}"
        )
        print(f"{ANSI_YELLOW}Author:{ANSI_RESET} @gastong256")
        print()
        print(f"{ANSI_BOLD}This demo walks the real gateway path end to end.{ANSI_RESET}")
        print(f"{ANSI_BLUE}•{ANSI_RESET} HTTP at the edge with FastAPI")
        print(f"{ANSI_BLUE}•{ANSI_RESET} Redis for cache and sliding-window rate limits")
        print(f"{ANSI_BLUE}•{ANSI_RESET} Stub LLM adapter behind a circuit breaker")
        print(f"{ANSI_BLUE}•{ANSI_RESET} Local sklearn wrappers for /v1/classify")
        print(
            f"{ANSI_BLUE}•{ANSI_RESET} Jaeger, Prometheus, and Grafana via the "
            "observability overlay"
        )

    def scene_context(self) -> None:
        health = self._health()
        print("Current health:")
        self._print_json(health)
        print()
        print("Useful URLs:")
        print(f"- /health: {self.config.base_url}/health")
        print(f"- Jaeger: {self.config.jaeger_url}")
        print(f"- Grafana: {self.config.grafana_url}")
        print()
        self._show_operator_notes(
            "Checklist:",
            [
                "Confirm Redis, circuit state, and loaded models in /health.",
                "Use Jaeger and Grafana as supporting views in the next scenes.",
            ],
        )

    def scene_infer_happy_path(self) -> None:
        response = self._post_infer(
            self.config.api_key,
            self._demo_prompt("Demo happy path: explain why cache misses increase latency."),
        )
        self._print_response_summary("Infer response", response)
        print()
        self._show_operator_notes(
            "Checklist:",
            [
                "Jaeger: latest inference-api trace with infer_flow and its child spans.",
                "Grafana: Request rate and Latency p50.",
            ],
        )

    def scene_cache_hit(self) -> None:
        prompt = self._demo_prompt("Demo cache hit: keep this prompt identical.")
        cold = self._post_infer(self.config.api_key, prompt)
        hot = self._post_infer(self.config.api_key, prompt)
        print("Cold call:")
        self._print_response_summary("cache miss", cold)
        print()
        print("Warm call:")
        self._print_response_summary("cache hit", hot)
        print()
        print(
            "Latency dropped from "
            f"~{cold['latency_ms']:.1f} ms to ~{hot['latency_ms']:.1f} ms "
            f"with cache_hit={hot['cache_hit']}."
        )
        print()
        self._show_operator_notes(
            "Checklist:",
            [
                "Jaeger: compare cold vs warm infer traces and call out cache.hit.",
                "Grafana: Cache hit signal and lower latency.",
            ],
        )

    def scene_classify_wrappers(self) -> None:
        noisy_input = "  pricing is not accurate!!!  "
        v1 = self._post_classify(noisy_input, "v1")
        v2 = self._post_classify(noisy_input, "v2")
        print(f"Input shown to the audience: {noisy_input!r}")
        print()
        self._print_json({"v1": v1, "v2": v2})
        print()
        print(
            "This scene shows the single classify path through the wrapper layer. "
            "The noisy input still works because preprocess lives with the model."
        )

    def scene_rate_limit(self) -> None:
        print("Using test-key-2 so the main demo key stays clean.")
        print("This scene keeps firing unique infer requests until the limiter returns 429.")
        print()
        first_ok: httpx.Response | None = None
        rejection: httpx.Response | None = None

        for attempt in range(1, 80):
            response = self._infer_response(
                self.config.rate_limit_key,
                f"rate limit demo request {attempt} at {time.time()}",
            )
            if response.status_code == 200 and first_ok is None:
                first_ok = response
            if response.status_code == 429:
                rejection = response
                print(f"429 reached on request {attempt}.")
                break

        if first_ok is None or rejection is None:
            raise RuntimeError("Could not demonstrate rate limiting within 79 requests.")

        print("Successful response headers:")
        self._print_json(self._pick_headers(first_ok))
        print()
        print("Rate-limited response:")
        self._print_json(
            {
                "status_code": rejection.status_code,
                "headers": self._pick_headers(rejection),
                "body": rejection.json(),
            }
        )
        print()
        self._show_operator_notes(
            "Checklist:",
            [
                "Compare the 200 and 429 headers, especially X-RateLimit-* and Retry-After.",
                "Grafana: Rate limit rejections.",
            ],
        )

    def scene_circuit_breaker(self) -> None:
        print("Restarting only the app container with STUB_FAILURE_RATE=1.0.")
        self._compose_up_service("app", {"STUB_FAILURE_RATE": "1.0"})
        self._wait_for_health()

        statuses: list[int] = []
        try:
            for attempt in range(1, 7):
                response = self._infer_response(
                    self.config.api_key,
                    f"circuit breaker demo request {attempt}",
                )
                statuses.append(response.status_code)

            open_health = self._health()
            print("Failure run status codes:")
            self._print_json({"statuses": statuses, "health": open_health})
        finally:
            print()
            print("Restoring the healthy stub and recreating the app container.")
            self._compose_up_service("app", {"STUB_FAILURE_RATE": "0.0"})
            self._wait_for_health(expected_redis="ok")

        recovered = self._post_infer(
            self.config.api_key,
            self._demo_prompt("Circuit recovery demo: the stub is healthy again."),
        )
        print()
        self._print_response_summary("Recovered infer", recovered)
        print()
        self._show_operator_notes(
            "Checklist:",
            [
                "Jaeger: failing infer trace, then fast rejection with the circuit open.",
                "Grafana: Circuit breaker opens, then recovered infer.",
            ],
        )

    def scene_redis_degradation(self) -> None:
        print("Stopping Redis and restarting only the app to show degraded behavior.")
        self._compose("stop", "redis")
        self._compose("restart", "app")
        self._wait_for_health(expected_redis="down")
        try:
            degraded_health = self._health()
            degraded_infer = self._infer_response(
                self.config.api_key,
                "Redis degradation demo request.",
            )
            self._print_json(
                {
                    "health": degraded_health,
                    "infer_status": degraded_infer.status_code,
                    "infer_body": degraded_infer.json(),
                }
            )
        finally:
            print()
            print("Restoring Redis and restarting the app to recover the normal path.")
            self._compose("up", "-d", "redis")
            self._compose("restart", "app")
            self._wait_for_health(expected_redis="ok")

        restored_health = self._health()
        self._print_json({"restored_health": restored_health})
        print()
        self._show_operator_notes(
            "Checklist:",
            [
                "Review the degraded /health payload and the 503 infer response.",
                "Classify would still work here; Jaeger and Grafana are secondary in this scene.",
            ],
        )

    def _pause(self, hint: str) -> None:
        print()
        print(f"Show next: {hint}")
        if self.config.auto:
            return
        input("Press Enter to continue...")

    def _next_hint(self, scene_number: int) -> str:
        hints = {
            0: "the healthy baseline and entry point",
            1: "the first /v1/infer response and the request shape",
            2: "the repeated request that turns into a cache hit",
            3: "the classify path and the wrapper-based model versions",
            4: "the rate-limit headers on both 200 and 429",
            5: "the circuit breaker opening after repeated provider failures",
            6: "Redis degradation and the health endpoint switching to degraded",
            7: "the end of the demo",
        }
        return hints[scene_number]

    def _banner(self, number: int, title: str) -> None:
        print()
        print("=" * 78)
        if number == 0:
            print(f"{ANSI_BOLD}{title}{ANSI_RESET}")
        else:
            print("llm-integration-api")
            print(f"Scene {number}: {title}")
        print("=" * 78)

    def _print_json(self, payload: Any) -> None:
        print(json.dumps(payload, indent=2, sort_keys=True))

    def _show_operator_notes(self, title: str, bullets: list[str]) -> None:
        print(title)
        for bullet in bullets:
            print(f"- {bullet}")

    def _demo_prompt(self, prompt: str) -> str:
        return f"{prompt} [demo:{self.demo_id}]"

    def _pick_headers(self, response: httpx.Response) -> dict[str, str]:
        wanted = (
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
            "X-Request-ID",
        )
        return {key: response.headers[key] for key in wanted if key in response.headers}

    def _print_response_summary(self, title: str, payload: dict[str, Any]) -> None:
        self._print_json(
            {
                "title": title,
                "request_id": payload["request_id"],
                "model": payload.get("model", payload.get("model_version")),
                "cache_hit": payload.get("cache_hit"),
                "label": payload.get("label"),
                "confidence": payload.get("confidence"),
                "usage": payload.get("usage"),
                "latency_ms": round(payload["latency_ms"], 2),
                "output": payload.get("output"),
            }
        )

    def _health(self) -> dict[str, Any]:
        return self._get_json(f"{self.config.base_url}/health")

    def _post_infer(self, api_key: str, prompt: str) -> dict[str, Any]:
        response = self._infer_response(api_key, prompt)
        response.raise_for_status()
        return response.json()

    def _infer_response(self, api_key: str, prompt: str) -> httpx.Response:
        return self.client.post(
            f"{self.config.base_url}/v1/infer",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"model": INFER_MODEL, "input": prompt},
        )

    def _post_classify(self, text: str, version: str) -> dict[str, Any]:
        response = self.client.post(
            f"{self.config.base_url}/v1/classify",
            headers={
                "X-API-Key": self.config.api_key,
                "X-Model-Version": version,
                "Content-Type": "application/json",
            },
            json={"input": text},
        )
        response.raise_for_status()
        return response.json()

    def _get_json(self, url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _wait_for_health(self, expected_redis: str | None = None, timeout_s: float = 90.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                health = self._health()
            except httpx.HTTPError:
                time.sleep(1.0)
                continue

            if expected_redis is not None and health.get("redis") != expected_redis:
                time.sleep(1.0)
                continue
            return
        raise TimeoutError("Application did not become healthy in time.")

    def _compose_up_service(self, service: str, extra_env: dict[str, str]) -> None:
        self._compose("up", "--build", "-d", service, env=extra_env)

    def _compose(self, *args: str, env: dict[str, str] | None = None) -> None:
        command = ["docker", "compose"]
        for compose_file in self.config.compose_files:
            command.extend(["-f", compose_file])
        command.extend(args)
        merged_env = os.environ.copy()
        if env is not None:
            merged_env.update(env)
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=merged_env,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "compose command failed"
            raise RuntimeError(message)


def parse_args() -> DemoConfig:
    parser = argparse.ArgumentParser(description="Guided live demo runner.")
    parser.add_argument("--auto", action="store_true", help="Run all scenes without pauses.")
    parser.add_argument("--scene", type=int, choices=range(0, 8), help="Run only one scene.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEMO_BASE_URL", "http://localhost:8000"),
        help="Inference API base URL.",
    )
    parser.add_argument(
        "--jaeger-url",
        default=os.getenv("DEMO_JAEGER_URL", "http://localhost:16686"),
        help="Jaeger base URL.",
    )
    parser.add_argument(
        "--grafana-url",
        default=os.getenv("DEMO_GRAFANA_URL", "http://localhost:3000"),
        help="Grafana base URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("DEMO_API_KEY", "test-key-1"),
        help="Primary demo API key.",
    )
    parser.add_argument(
        "--rate-limit-key",
        default=os.getenv("DEMO_RATE_LIMIT_KEY", "test-key-2"),
        help="Separate API key used in the rate-limit scene.",
    )
    args = parser.parse_args()
    return DemoConfig(
        base_url=args.base_url.rstrip("/"),
        jaeger_url=args.jaeger_url.rstrip("/"),
        grafana_url=args.grafana_url.rstrip("/"),
        api_key=args.api_key,
        rate_limit_key=args.rate_limit_key,
        auto=args.auto,
        scene=args.scene,
        compose_files=DEFAULT_COMPOSE_FILES,
    )


def main() -> int:
    try:
        DemoRunner(parse_args()).run()
    except (httpx.HTTPError, RuntimeError, subprocess.CalledProcessError, TimeoutError) as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
