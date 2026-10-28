"""Facebook Page photo publishing with explicit delivery outcomes.

An upload is deliberately never retried here. A lost response can mean Meta
accepted the photo, so callers must reconcile an unknown outcome before retrying.
"""
from __future__ import annotations

import os
import re

import requests


class FacebookError(RuntimeError):
    """A configuration, access, or explicitly rejected Facebook request."""


class FacebookDeliveryUnknown(FacebookError):
    """The upload may have succeeded; do not automatically repost it."""


class FacebookClient:
    def __init__(self, page_id: str, token: str, version: str = "v26.0"):
        self.page_id = page_id.strip()
        self.token = token.strip()
        self.version = version.strip()
        if not re.fullmatch(r"[0-9]+", self.page_id):
            raise FacebookError("META_PAGE_ID must be the numeric Facebook Page ID.")
        if not self.token:
            raise FacebookError("META_PAGE_ACCESS_TOKEN is missing; supply a Page access token.")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", self.version):
            raise FacebookError("META_GRAPH_VERSION must have the form v26.0.")

    @classmethod
    def from_env(cls) -> "FacebookClient":
        return cls(os.getenv("META_PAGE_ID", ""), os.getenv("META_PAGE_ACCESS_TOKEN", ""),
                   os.getenv("META_GRAPH_VERSION", "v26.0"))

    @property
    def _url(self) -> str:
        return f"https://graph.facebook.com/{self.version}/{self.page_id}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _safe(self, value: object) -> str:
        text = str(value).replace(self.token, "[REDACTED]")
        text = re.sub(r"(?i)(access_token\s*[=:]\s*)[^\s&\"']+", r"\1[REDACTED]", text)
        text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
        return " ".join(text.split())[:1000]

    def _decode(self, response: requests.Response, *, upload: bool) -> dict:
        status = response.status_code
        failure = FacebookDeliveryUnknown if upload and (status >= 500 or status == 408) else FacebookError
        try:
            payload = response.json()
        except ValueError:
            failure = FacebookDeliveryUnknown if upload and 200 <= status < 300 else failure
            raise failure(f"Facebook returned HTTP {status} with a non-JSON response.") from None
        if not isinstance(payload, dict):
            failure = FacebookDeliveryUnknown if upload and 200 <= status < 300 else failure
            raise failure(f"Facebook returned HTTP {status} with an invalid response.")
        error = payload.get("error")
        if error or not 200 <= status < 300:
            details = [f"Facebook HTTP {status}"]
            if isinstance(error, dict):
                for key in ("message", "code", "error_subcode", "fbtrace_id"):
                    if key in error:
                        details.append(f"{key}={self._safe(error[key])}")
            raise failure("; ".join(details))
        return payload

    def check_page(self) -> dict:
        """Confirm the token belongs to this Page; this does not prove write access."""
        try:
            response = requests.get(f"https://graph.facebook.com/{self.version}/me", headers=self._headers,
                                    params={"fields": "id,name"}, timeout=(10, 30),
                                    allow_redirects=False)
        except requests.RequestException as exc:
            raise FacebookError(f"Facebook Page check failed ({type(exc).__name__}); check connectivity.") from None
        payload = self._decode(response, upload=False)
        if str(payload.get("id", "")) != self.page_id:
            raise FacebookError("Facebook did not confirm the configured Page ID; verify the Page token and ID.")
        return payload

    def publish_photo(self, image_path: str, caption: str) -> str:
        with open(image_path, "rb") as image:
            try:
                response = requests.post(self._url + "/photos", headers=self._headers,
                                         data={"caption": caption, "published": "true"},
                                         files={"source": image}, timeout=(10, 60),
                                         allow_redirects=False)
            except requests.RequestException as exc:
                raise FacebookDeliveryUnknown(
                    f"Facebook upload response was lost ({type(exc).__name__}). "
                    "Check the Page before retrying; the photo may already be published."
                ) from None
        payload = self._decode(response, upload=True)
        reference = payload.get("post_id") or payload.get("id")
        if not isinstance(reference, str) or not re.fullmatch(r"[0-9]+(?:_[0-9]+)?", reference):
            raise FacebookDeliveryUnknown(
                "Facebook accepted the request without a usable post/photo ID. "
                "Check the Page before retrying."
            )
        return reference
