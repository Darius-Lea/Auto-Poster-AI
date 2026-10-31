"""Facebook Page photo publishing with explicit delivery outcomes.

An upload is deliberately never retried here. A lost response can mean Meta
accepted the photo, so callers must reconcile an unknown outcome before retrying.
"""
from __future__ import annotations

import os
import re
import json
from contextlib import ExitStack

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

    def _target(self, remote_ref: str) -> tuple[str, str]:
        """Resolve a stored photo/post reference without accepting paths or URLs."""
        if not isinstance(remote_ref, str):
            raise FacebookError("A stored Facebook photo or post ID is required.")
        reference = remote_ref.strip()
        if reference.startswith("meta:"):
            reference = reference[len("meta:"):]
        if not re.fullmatch(r"[0-9]+(?:_[0-9]+)?", reference):
            raise FacebookError("The Facebook reference must be a numeric photo ID or Page_post ID.")
        if "_" in reference:
            if reference.split("_", 1)[0] != self.page_id:
                raise FacebookError("The stored Facebook post belongs to a different Page.")
            return reference, "message"
        return reference, "name"

    def _owned_caption(self, reference: str, field: str) -> str:
        try:
            response = requests.get(
                f"https://graph.facebook.com/{self.version}/{reference}",
                headers=self._headers, params={"fields": f"id,{field},from{{id}}"},
                timeout=(10, 30), allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FacebookError(
                f"Facebook caption check failed ({type(exc).__name__}); check connectivity."
            ) from None
        payload = self._decode(response, upload=False)
        owner = payload.get("from")
        if (str(payload.get("id", "")) != reference or not isinstance(owner, dict)
                or str(owner.get("id", "")) != self.page_id):
            raise FacebookError("Facebook did not confirm this content belongs to the configured Page.")
        caption = payload.get(field, "")
        if not isinstance(caption, str):
            raise FacebookError("Facebook returned an invalid caption; no edit was made.")
        return caption

    def get_caption(self, remote_ref: str) -> str:
        """Read a stored post/photo caption only after confirming Page ownership."""
        reference, field = self._target(remote_ref)
        return self._owned_caption(reference, field)

    def update_caption(self, remote_ref: str, caption: str, *, expected_caption: str | None = None) -> None:
        """Edit an existing owned post/photo in place; never create or delete one.

        Reapplying the same caption is idempotent. A lost response is still
        reported as uncertain so callers do not record an unconfirmed edit.
        """
        if not isinstance(caption, str):
            raise FacebookError("The updated Facebook caption must be text.")
        reference, field = self._target(remote_ref)
        current = self._owned_caption(reference, field)
        if expected_caption is not None and current != expected_caption:
            raise FacebookError("The caption changed since the roster check; no edit was made. Retry after reviewing the current text.")
        try:
            response = requests.post(
                f"https://graph.facebook.com/{self.version}/{reference}",
                headers=self._headers, data={field: caption}, timeout=(10, 30),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FacebookDeliveryUnknown(
                f"Facebook caption update response was lost ({type(exc).__name__}); "
                "the edit is not confirmed."
            ) from None
        payload = self._decode(response, upload=True)
        if payload.get("success") is not True:
            raise FacebookDeliveryUnknown("Facebook did not confirm the caption update succeeded.")

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

    def publish_comment(self, post_ref: str, message: str) -> str:
        """Publish one comment on a confirmed post belonging to this Page."""
        reference, field = self._target(post_ref)
        if field != "message" or "_" not in reference:
            raise FacebookError("Comments can only be added to a confirmed Page post.")
        if not isinstance(message, str) or not message.strip():
            raise FacebookError("A non-empty comment is required.")
        try:
            response = requests.post(
                f"https://graph.facebook.com/{self.version}/{reference}/comments",
                headers=self._headers, data={"message": message},
                timeout=(10, 30), allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FacebookDeliveryUnknown(
                f"Facebook comment response was lost ({type(exc).__name__}); "
                "check the post before retrying."
            ) from None
        payload = self._decode(response, upload=True)
        comment_id = payload.get("id")
        if not isinstance(comment_id, str) or not re.fullmatch(r"[0-9]+(?:_[0-9]+)?", comment_id):
            raise FacebookDeliveryUnknown(
                "Facebook accepted the comment request without a usable comment ID; "
                "check the post before retrying."
            )
        return comment_id

    def publish_group(self, items: list[dict], group_caption: str) -> dict:
        """Publish up to ten individually captioned photos as one Page post.

        All photos are uploaded unpublished before creating the feed post.
        Failures expose only known photo IDs and the stage for reconciliation;
        no request is retried and unpublished uploads are never deleted here.
        """
        photo_ids: list[str] = []
        stage = "upload"
        try:
            if not isinstance(items, list) or not 1 <= len(items) <= 10:
                raise FacebookError("A Facebook group must contain between one and ten photos.")
            if not isinstance(group_caption, str):
                raise FacebookError("The group caption must be text.")
            for item in items:
                if (not isinstance(item, dict)
                        or not isinstance(item.get("image_path"), (str, os.PathLike))
                        or not isinstance(item.get("caption"), str)):
                    raise FacebookError("Each group photo needs an image path and a text caption.")
            with ExitStack() as stack:
                try:
                    # Open every image before uploading any, so a missing later
                    # file cannot leave a partially uploaded group unnecessarily.
                    images = [stack.enter_context(open(item["image_path"], "rb")) for item in items]
                except OSError:
                    raise FacebookError("Could not open all group images; no upload was attempted.") from None
                for item, image_file in zip(items, images):
                    try:
                        response = requests.post(
                            self._url + "/photos", headers=self._headers,
                            data={"caption": item["caption"], "published": "false"},
                            files={"source": image_file}, timeout=(10, 60),
                            allow_redirects=False,
                        )
                    except requests.RequestException as exc:
                        raise FacebookDeliveryUnknown(
                            f"Facebook group photo response was lost ({type(exc).__name__}); "
                            "reconcile the uploaded photos before retrying."
                        ) from None
                    payload = self._decode(response, upload=True)
                    photo_id = payload.get("id")
                    if (not isinstance(photo_id, str) or not re.fullmatch(r"[0-9]+", photo_id)
                            or photo_id in photo_ids):
                        raise FacebookDeliveryUnknown(
                            "Facebook did not return a usable unique group photo ID; "
                            "reconcile the uploaded photos before retrying."
                        )
                    photo_ids.append(photo_id)
                stage = "feed"
                try:
                    response = requests.post(
                        self._url + "/feed", headers=self._headers,
                        data={"message": group_caption,
                              "attached_media": json.dumps([{"media_fbid": photo_id} for photo_id in photo_ids])},
                        timeout=(10, 60), allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    raise FacebookDeliveryUnknown(
                        f"Facebook group post response was lost ({type(exc).__name__}); "
                        "check the Page before retrying because the group may already be published."
                    ) from None
                payload = self._decode(response, upload=True)
                post_id = payload.get("id")
                if (not isinstance(post_id, str)
                        or not re.fullmatch(re.escape(self.page_id) + r"_[0-9]+", post_id)):
                    raise FacebookDeliveryUnknown(
                        "Facebook did not return a usable group post ID for this Page; "
                        "check the Page before retrying."
                    )
                return {"post_id": post_id, "photo_ids": photo_ids}
        except FacebookError as exc:
            exc.uploaded_photo_ids = list(photo_ids)
            exc.stage = stage
            raise
