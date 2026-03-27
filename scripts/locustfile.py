import os
import random

from locust import HttpUser, constant_pacing, task

API_KEY = os.getenv("LOCUST_API_KEY", "test-key-1")
INFER_MODEL = os.getenv("LOCUST_INFER_MODEL", "gpt-4o-mini")
CLASSIFY_VERSION = os.getenv("LOCUST_CLASSIFY_VERSION", "v1")

INFER_INPUTS = [
    "Summarize quarterly sales performance.",
    "Explain why cache misses can increase latency.",
    "List three risks of a shared Redis outage.",
    "Write a short response for a customer support ticket.",
]

CLASSIFY_INPUTS = [
    "payment approved",
    "request not approved",
    "shipment not delayed",
    "service is not working",
]


class InferUser(HttpUser):
    wait_time = constant_pacing(0.05)

    @task
    def infer(self) -> None:
        self.client.post(
            "/v1/infer",
            headers={"X-API-Key": API_KEY},
            json={
                "model": INFER_MODEL,
                "input": random.choice(INFER_INPUTS),
                "config": {"temperature": 0.1},
            },
            name="/v1/infer",
        )


class ClassifyUser(HttpUser):
    wait_time = constant_pacing(0.05)

    @task
    def classify(self) -> None:
        self.client.post(
            "/v1/classify",
            headers={
                "X-API-Key": API_KEY,
                "X-Model-Version": CLASSIFY_VERSION,
            },
            json={"input": random.choice(CLASSIFY_INPUTS)},
            name="/v1/classify",
        )
