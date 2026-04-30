import os
import random

from locust import HttpUser, constant_pacing, task

DEFAULT_API_KEY = "test-key-1"
DEFAULT_INFER_MODEL = "gpt-4o-mini"
DEFAULT_CLASSIFY_VERSION = "v1"
DEFAULT_INFER_WEIGHT = 1
DEFAULT_CLASSIFY_WEIGHT = 1
DEFAULT_INFER_INPUT_MODE = "mixed"
VALID_INFER_INPUT_MODES = {"mixed", "single"}

API_KEY = os.getenv("LOCUST_API_KEY", DEFAULT_API_KEY)
INFER_MODEL = os.getenv("LOCUST_INFER_MODEL", DEFAULT_INFER_MODEL)
CLASSIFY_VERSION = os.getenv("LOCUST_CLASSIFY_VERSION", DEFAULT_CLASSIFY_VERSION)
INFER_WEIGHT = int(os.getenv("LOCUST_INFER_WEIGHT", str(DEFAULT_INFER_WEIGHT)))
CLASSIFY_WEIGHT = int(os.getenv("LOCUST_CLASSIFY_WEIGHT", str(DEFAULT_CLASSIFY_WEIGHT)))
INFER_INPUT_MODE = os.getenv("LOCUST_INFER_INPUT_MODE", DEFAULT_INFER_INPUT_MODE).lower()
if INFER_INPUT_MODE not in VALID_INFER_INPUT_MODES:
    INFER_INPUT_MODE = DEFAULT_INFER_INPUT_MODE

INFER_INPUTS = [
    "Summarize quarterly sales performance.",
    "Explain why cache misses can increase latency.",
    "List three risks of a shared Redis outage.",
    "Write a short response for a customer support ticket.",
]
SINGLE_INFER_INPUT = "Explain why cache misses can increase latency."

CLASSIFY_INPUTS = [
    "pricing looks accurate",
    "promotion is not effective",
    "  STOCK   looks healthy!!  ",
    "store data is not clean",
]


def pick_infer_input() -> str:
    if INFER_INPUT_MODE == "single":
        return SINGLE_INFER_INPUT
    return random.choice(INFER_INPUTS)


class InferUser(HttpUser):
    wait_time = constant_pacing(0.05)
    weight = INFER_WEIGHT

    @task
    def infer(self) -> None:
        self.client.post(
            "/v1/infer",
            headers={"X-API-Key": API_KEY},
            json={
                "model": INFER_MODEL,
                "input": pick_infer_input(),
                "config": {"temperature": 0.1},
            },
            name="/v1/infer",
        )


class ClassifyUser(HttpUser):
    wait_time = constant_pacing(0.05)
    weight = CLASSIFY_WEIGHT

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
