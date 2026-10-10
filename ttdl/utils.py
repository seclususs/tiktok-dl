import os
from typing import Any


def parse_url_input(target: str) -> list[str]:
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8") as f:
            return [
                line.strip() for line in f if line.strip() and not line.startswith("#")
            ]
    return [target]


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

    vid_id = str(node.get("id", node.get("item_id", node.get("video_id", ""))))
    if vid_id.isdigit() and len(vid_id) >= 15:
        author_val = node.get("author")
        author_name = ""
        if isinstance(author_val, dict):
            author_name = str(
                author_val.get("uniqueId") or author_val.get("unique_id") or ""
            )
        elif isinstance(author_val, str):
            author_name = author_val

        target_clean = target_username.lstrip("@").lower() if target_username else ""
        if (
            target_clean
            and author_name
            and author_name.lstrip("@").lower() != target_clean
        ):
            for value in node.values():
                extract_video_nodes(value, collection, target_username)
            return

        c_time = node.get("createTime") or node.get("create_time")
        c_time_int = int(c_time) if c_time else 0
        if not c_time_int and vid_id.isdigit():
            c_time_int = int(vid_id) >> 32

        if c_time_int and ("video" in node or "imagePost" in node):
            post_type = "photo" if "imagePost" in node else "video"
            duration = 0
            vid_data = node.get("video")
            if isinstance(vid_data, dict):
                duration = int(vid_data.get("duration", 0))

            try:
                if vid_id in collection:
                    collection[vid_id]["createTime"] = c_time_int
                    collection[vid_id]["post_type"] = post_type
                    collection[vid_id]["duration"] = duration
                    if "author" in node:
                        collection[vid_id]["author"] = node["author"]
                else:
                    collection[vid_id] = {
                        "id": vid_id,
                        "createTime": c_time_int,
                        "author": node.get("author"),
                        "post_type": post_type,
                        "duration": duration,
                    }
            except ValueError:
                pass
    for value in node.values():
        extract_video_nodes(value, collection, target_username)
