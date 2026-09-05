"""Model providers behind one interface, so the benchmark is not tied to a vendor.

The question this project asks is whether a language model beats hand written
rules, not whether one particular vendor's model does. `provider_for` picks a
provider from the model id alone, so adding a second one is a new class here,
not a change anywhere else, and every result row records which provider
produced it so a number can never be read as belonging to a model that did not
generate it.

Vertex AI, serving Google's Gemini models through the OpenAI compatible
endpoint, is the one implemented. This path has no static key. It authenticates
with a short lived access token from the gcloud CLI, which means the machine
running it needs `gcloud auth login` already done and a project id in the
environment. The token is cached and refreshed on expiry rather than fetched
once, because a full run over this corpus can outlast a token's lifetime and a
mid run 401 would otherwise be recorded as a model failure.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import time
from dataclasses import dataclass

import requests

from .config import load_dotenv

# gcloud access tokens last about an hour. Refresh well before that, since the
# cost of an extra refresh is one subprocess call and the cost of being wrong is
# a failed extraction that looks like a model error.
_TOKEN_TTL_SECONDS = 45 * 60
_VERTEX_TIMEOUT_SECONDS = 300

# A sustained run over a corpus this size hits the endpoint's rate limit, and a
# rate limited call is an infrastructure event, not a model failure. Recording it
# as a failed extraction would understate the model's accuracy for reasons that
# have nothing to do with the model, so these are retried with exponential
# backoff and only a call that exhausts every attempt is allowed to fail.
_RETRY_STATUSES = (408, 429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 6
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 60.0


class NoEndpoint(RuntimeError):
    """Raised instead of returning fabricated predictions."""


@dataclass
class ModelReply:
    text: str
    input_tokens: int
    output_tokens: int
    model_returned: str


def provider_for(model: str) -> str:
    """Which provider serves this model id. The id carries the answer."""
    return "vertex"


class VertexProvider:
    name = "vertex"

    def __init__(self) -> None:
        load_dotenv()
        project = os.environ.get("GCP_PROJECT_ID", "").strip()
        if not project:
            raise NoEndpoint(
                "No GCP_PROJECT_ID set. Vertex needs a project id and a gcloud "
                "login on this machine. Copy .env.example to .env and set it."
            )
        self._project = project
        self._region = os.environ.get("GCP_REGION", "us-central1").strip()
        self._token = ""
        self._token_fetched_at = 0.0
        self._url = (
            f"https://{self._region}-aiplatform.googleapis.com/v1beta1/projects/"
            f"{self._project}/locations/{self._region}/endpoints/openapi/chat/completions"
        )

    def _access_token(self) -> str:
        if self._token and time.time() - self._token_fetched_at < _TOKEN_TTL_SECONDS:
            return self._token
        try:
            token = subprocess.check_output(
                ["gcloud", "auth", "print-access-token"],
                stderr=subprocess.PIPE,
            ).decode().strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise NoEndpoint(
                "Could not get a gcloud access token. Run `gcloud auth login` first."
            ) from exc
        if not token:
            raise NoEndpoint("gcloud returned an empty access token.")
        self._token = token
        self._token_fetched_at = time.time()
        return token

    def _post(self, payload: dict) -> requests.Response:
        return requests.post(
            self._url,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=_VERTEX_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _sleep_for(attempt: int, response: requests.Response | None) -> float:
        """How long to wait before the next attempt.

        The server's own Retry-After is obeyed where it sends one. Otherwise
        exponential backoff with jitter, since several workers backing off in
        lockstep would simply collide again.
        """
        if response is not None:
            hint = response.headers.get("Retry-After", "")
            if hint.strip().isdigit():
                return min(float(hint.strip()), _BACKOFF_CAP_SECONDS)
        wait = min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _BACKOFF_CAP_SECONDS)
        return wait * (0.5 + random.random() / 2)

    def complete(self, system: str, user: str, max_tokens: int,
                 temperature: float) -> ModelReply:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self._post(payload)
            except requests.RequestException as exc:
                # Connection level failure, also transient and also not the
                # model's fault.
                last_error = exc
                time.sleep(self._sleep_for(attempt, None))
                continue

            if response.status_code == 401:
                # Token expired inside a long run. Refresh and retry rather than
                # recording it as a model failure.
                self._token = ""
                last_error = requests.HTTPError("401 from Vertex, token refreshed")
                continue

            if response.status_code in _RETRY_STATUSES:
                last_error = requests.HTTPError(
                    f"{response.status_code} from Vertex, attempt {attempt + 1}"
                )
                time.sleep(self._sleep_for(attempt, response))
                continue

            response.raise_for_status()
            body = response.json()
            usage = body.get("usage", {})
            return ModelReply(
                text=body["choices"][0]["message"].get("content") or "",
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                model_returned=body.get("model", self.model),
            )

        raise RuntimeError(
            f"Vertex call failed after {_MAX_ATTEMPTS} attempts: {last_error}"
        )


def get_provider(model: str):
    """The provider that serves this model id, already constructed."""
    provider_for(model)
    provider = VertexProvider()
    provider.model = model
    return provider
