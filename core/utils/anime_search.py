import os
from pathlib import Path
import urllib.parse
import aiohttp
from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Node, Nodes, Plain
from astrbot.core.star.star_tools import StarTools


def time_convert(t: float | int) -> str:
    m, s = divmod(t, 60)
    return f"{int(m)}分{int(s)}秒"


async def search_anime_by_image(
    image_url: str = "",
    image_bytes: bytes = None,
    limit: int = 3,
    bot_id: str = "",
    bot_name: str = "Giftia",
) -> tuple[bool, MessageChain, str]:
    """
    通过 trace.moe API 检索番剧信息。
    支持返回多条结果并以合并转发 Nodes 格式输出。
    返回: (is_success, MessageChain, err_msg)
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        data = None
        async with aiohttp.ClientSession(headers=headers) as session:
            # 若传了 image_url 且没有 image_bytes，判断是本地文件路径还是 HTTP URL
            if image_url and not image_bytes:
                clean_path = (
                    image_url[7:] if image_url.startswith("file://") else image_url
                )
                if os.path.exists(clean_path):
                    try:
                        image_bytes = Path(clean_path).read_bytes()
                    except Exception as e:
                        logger.error(f"[Giftia] 读取本地图片失败 {clean_path}: {e}")
                elif image_url.startswith(("http://", "https://")):
                    try:
                        async with session.get(image_url) as img_resp:
                            if img_resp.status == 200:
                                image_bytes = await img_resp.read()
                    except Exception as e:
                        logger.warning(f"[Giftia] 本地拉取搜番图片 URL 失败: {e}")

            if image_bytes:
                url = "https://api.trace.moe/search?anilistInfo"
                post_headers = {"Content-Type": "image/jpeg", **headers}
                async with session.post(url, data=image_bytes, headers=post_headers) as resp:
                    if resp.status != 200:
                        err_text = ""
                        try:
                            res_json = await resp.json()
                            err_text = res_json.get("error", "")
                        except Exception:
                            err_text = await resp.text()
                        err_detail = err_text[:100] if err_text else f"HTTP {resp.status}"
                        return (
                            False,
                            MessageChain([Plain(f"搜番请求失败 (HTTP {resp.status}): {err_detail}")]),
                            f"HTTP {resp.status}: {err_detail}",
                        )
                    data = await resp.json()
            elif image_url:
                url = f"https://api.trace.moe/search?anilistInfo&url={urllib.parse.quote(image_url)}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        err_text = ""
                        try:
                            res_json = await resp.json()
                            err_text = res_json.get("error", "")
                        except Exception:
                            err_text = await resp.text()
                        err_detail = err_text[:100] if err_text else f"HTTP {resp.status}"
                        return (
                            False,
                            MessageChain([Plain(f"搜番请求失败 (HTTP {resp.status}): {err_detail}")]),
                            f"HTTP {resp.status}: {err_detail}",
                        )
                    data = await resp.json()
            else:
                return (
                    False,
                    MessageChain([Plain("缺少有效的图片数据")]),
                    "No image provided",
                )

        if data and data.get("result") and len(data["result"]) > 0:
            raw_results = data["result"]
            max_count = max(1, min(int(limit or 3), 10))
            target_results = raw_results[:max_count]

            nodes = []
            uploader_uin = bot_id or "10000"
            uploader_name = bot_name or "Giftia"

            # 头部节点
            header_node = Node(
                uin=uploader_uin,
                name=uploader_name,
                content=[
                    Plain(
                        f"🔍 以图搜番结果 (trace.moe)\n"
                        f"共包含 {len(target_results)} 个候选结果："
                    )
                ],
            )
            nodes.append(header_node)

            for idx, top_result in enumerate(target_results, 1):
                from_str = time_convert(top_result.get("from", 0))
                to_str = time_convert(top_result.get("to", 0))
                similarity = float(top_result.get("similarity", 0))

                warn = ""
                if similarity < 0.8:
                    warn = "⚠️ 相似度较低，可能非相同画面/剧集\n"

                anilist = top_result.get("anilist") or {}
                title_dict = anilist.get("title") or {}
                title = (
                    title_dict.get("native")
                    or title_dict.get("chinese")
                    or title_dict.get("romaji")
                    or title_dict.get("english")
                    or "未知番剧"
                )
                episode = top_result.get("episode") or "未知"
                shot_image_url = top_result.get("image", "")

                sim_percent = f"{similarity * 100:.1f}%"

                node_text = (
                    f"【结果 #{idx}】\n"
                    f"{warn}"
                    f"番名: {title}\n"
                    f"相似度: {sim_percent}\n"
                    f"剧集: 第{episode}集\n"
                    f"时间: {from_str} - {to_str}\n"
                    f"精准截图:"
                )

                node_content = [Plain(node_text)]
                if shot_image_url:
                    node_content.append(Image.fromURL(shot_image_url))

                nodes.append(
                    Node(
                        uin=uploader_uin,
                        name=f"结果 #{idx} | {title[:20]}",
                        content=node_content,
                    )
                )

            return True, MessageChain([Nodes(nodes)]), ""
        else:
            return (
                False,
                MessageChain([Plain("🧐 没有识别到相关番剧信息的喵")]),
                "No result found",
            )
    except Exception as e:
        logger.error(f"[Giftia] 以图搜番异常: {e}", exc_info=True)
        return False, MessageChain([Plain(f"搜番时发生错误: {e}")]), str(e)


async def search_anime_by_media_id(
    plugin,
    media_id: str,
    limit: int = 3,
    bot_id: str = "",
    bot_name: str = "Giftia",
) -> tuple[bool, MessageChain, str]:
    """
    根据 media_id (或 hash_val) 查找图片数据并执行搜番。
    """
    cache_file = (
        StarTools.get_data_dir("astrbot_plugin_giftia")
        / "media_cache"
        / media_id
    )
    if cache_file.exists():
        try:
            image_bytes = cache_file.read_bytes()
            return await search_anime_by_image(
                image_bytes=image_bytes,
                limit=limit,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        except Exception as e:
            logger.error(f"[Giftia] 读取媒体缓存 {media_id} 失败: {e}")

    media_caption = await plugin.data_cache.get_caption_by_hash(media_id)
    if media_caption and media_caption.url:
        return await search_anime_by_image(
            image_url=media_caption.url,
            limit=limit,
            bot_id=bot_id,
            bot_name=bot_name,
        )

    return (
        False,
        MessageChain(
            [Plain(f"未找到 media_id `{media_id}` 对应的缓存图片或 URL")]
        ),
        "Media not found",
    )
