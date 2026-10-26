import os
from typing import Any, Final

SNOWFLAKE_TIMESTAMP_SHIFT: Final[int] = 32
MIN_TIKTOK_ID_LENGTH: Final[int] = 15
ID_KEYS: Final[tuple[str, ...]] = ("id", "item_id", "video_id")
TIMESTAMP_KEYS: Final[tuple[str, ...]] = ("createTime", "create_time")


def parse_url_input(target: str) -> list[str]:
    if not os.path.isfile(target):
        return [target]

    with open(target, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def _extract_node_id(node: dict[str, Any]) -> str:
    for key in ID_KEYS:
        val = node.get(key)
        if val:
            val_str = str(val).strip()
            if val_str.isdigit() and len(val_str) >= MIN_TIKTOK_ID_LENGTH:
                return val_str

    return ""


def _extract_node_author(node: dict[str, Any]) -> str:
    author_val = node.get("author")

    if isinstance(author_val, dict):
        return str(
            author_val.get("uniqueId") or author_val.get("unique_id") or ""
        ).strip()

    if isinstance(author_val, str):
        return author_val.strip()

    return ""


def _matches_target_user(author_name: str, target_username: str | None) -> bool:
    if not target_username or not author_name:
        return True
    return author_name.lstrip("@").lower() == target_username.lstrip("@").lower()


def _extract_node_timestamp(node: dict[str, Any], vid_id: str) -> int:
    for key in TIMESTAMP_KEYS:
        c_time = node.get(key)
        if c_time:
            try:
                c_time_int = int(c_time)
                if c_time_int > 0:
                    return c_time_int
            except (ValueError, TypeError):
                continue

    if vid_id.isdigit():
        return int(vid_id) >> SNOWFLAKE_TIMESTAMP_SHIFT

    return 0


def _extract_node_media(node: dict[str, Any]) -> tuple[str, int] | None:
    has_video = "video" in node
    has_image = "imagePost" in node
    if not (has_video or has_image):
        return None

    post_type = "photo" if has_image else "video"
    duration = 0
    vid_data = node.get("video")
    if isinstance(vid_data, dict):
        try:
            duration = int(vid_data.get("duration", 0))
        except (ValueError, TypeError):
            duration = 0

    return post_type, duration


def _record_item_data(
    node: dict[str, Any],
    vid_id: str,
    timestamp: int,
    media_info: tuple[str, int],
    collection: dict[str, dict[str, Any]],
) -> None:
    post_type, duration = media_info
    author_val = node.get("author")

    if vid_id in collection:
        existing = collection[vid_id]
        existing["createTime"] = timestamp
        existing["post_type"] = post_type
        existing["duration"] = duration
        if author_val is not None:
            existing["author"] = author_val
    else:
        collection[vid_id] = {
            "id": vid_id,
            "createTime": timestamp,
            "author": author_val,
            "post_type": post_type,
            "duration": duration,
        }


def extract_video_nodes(
    node: Any,
    collection: dict[str, dict[str, Any]],
    target_username: str | None = None,
) -> None:
    if isinstance(node, list):
        for item in node:
            extract_video_nodes(item, collection, target_username)
        return

    if not isinstance(node, dict):
        return

    vid_id = _extract_node_id(node)
    if vid_id:
        author = _extract_node_author(node)

        if _matches_target_user(author, target_username):
            timestamp = _extract_node_timestamp(node, vid_id)
            media_info = _extract_node_media(node)

            if timestamp > 0 and media_info is not None:
                _record_item_data(node, vid_id, timestamp, media_info, collection)

    for value in node.values():
        extract_video_nodes(value, collection, target_username)
