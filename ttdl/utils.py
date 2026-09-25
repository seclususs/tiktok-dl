from typing import Any


def extract_video_nodes(
    node: Any,
    collection: dict[str, dict[str, Any]],
) -> None:
    if isinstance(node, list):
        for item in node:
            extract_video_nodes(item, collection)
        return

    if not isinstance(node, dict):
        return

    vid_id = str(node.get("id", node.get("item_id", node.get("video_id", ""))))
    if vid_id.isdigit() and len(vid_id) >= 15:
        c_time = node.get("createTime") or node.get("create_time")
        if c_time and ("video" in node or "imagePost" in node):
            post_type = "photo" if "imagePost" in node else "video"
            duration = 0
            vid_data = node.get("video")
            if isinstance(vid_data, dict):
                duration = int(vid_data.get("duration", 0))

            try:
                c_time_int = int(c_time)
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
        extract_video_nodes(value, collection)
