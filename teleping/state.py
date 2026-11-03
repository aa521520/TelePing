"""Encrypted durable state on a dedicated branch; compare-and-swap writes."""

import base64
import re

import httpx

from .crypto import decrypt, encrypt
from .targets import parse_targets


def new_state(seed_text: str = "") -> dict:
    return {"version": 1, "interval_minutes": 30, "last_update_id": -1, "targets": parse_targets(seed_text), "forced_targets": [], "force_all": False, "pending_targets": [], "last_check_started": 0.0, "site_states": {}, "expiry_cache": {}, "outbox": [], "last_summary": {}, "menu_registered": False}


class GitHubStateStore:
    def __init__(self, repository: str, token: str, key: str, *, client: httpx.Client | None = None, default_branch: str = "main"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository_invalid")
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token
        self.key = key
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.default_branch = default_branch
        self.sha = None
        self.branch = "teleping-state"

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self.client.request(method, self.base + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise RuntimeError("github_unavailable") from None
        if response.status_code in {409, 422}:
            raise RuntimeError("github_state_conflict")
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError("github_request_failed")
        return response

    def load(self) -> dict | None:
        response = self._request("GET", f"/contents/state.enc?ref={self.branch}", headers={"Accept": "application/vnd.github.object+json"})
        if response.status_code == 404:
            self.sha = None
            return None
        try:
            payload = response.json()
            content = payload
            if payload.get("encoding") == "none":
                content = self._request("GET", f"/git/blobs/{payload['sha']}").json()
            value = decrypt(base64.b64decode(content["content"]), self.key, purpose="state")
            if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("targets"), list):
                raise ValueError("encrypted_data_invalid")
            self.sha = payload["sha"]
            return value
        except (KeyError, TypeError, ValueError):
            raise ValueError("encrypted_data_invalid") from None

    def _ensure_branch(self) -> None:
        if self._request("GET", f"/git/ref/heads/{self.branch}").status_code != 404:
            return
        response = self._request("GET", f"/git/ref/heads/{self.default_branch}")
        if response.status_code == 404:
            raise RuntimeError("default_branch_missing")
        self._request("POST", "/git/refs", json={"ref": f"refs/heads/{self.branch}", "sha": response.json()["object"]["sha"]})

    def save(self, state: dict) -> None:
        if self.sha is None:
            self._ensure_branch()
        payload = {"message": "Update encrypted monitor state", "branch": self.branch, "content": base64.b64encode(encrypt(state, self.key, purpose="state")).decode("ascii")}
        if self.sha:
            payload["sha"] = self.sha
        response = self._request("PUT", "/contents/state.enc", json=payload)
        if response.status_code == 404:
            raise RuntimeError("state_branch_missing")
        self.sha = response.json()["content"]["sha"]
