import os
from pathlib import Path
import urllib.parse
import aiohttp
from bs4 import BeautifulSoup

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Node, Nodes, Plain
from astrbot.core.star.star_tools import StarTools


async def search_illust_by_image(
    image_url: str = "",
    image_bytes: bytes = None,
    limit: int = 3,
    api_key: str = "",
    bot_id: str = "",
    bot_name: str = "Giftia",
) -> tuple[bool, MessageChain, str]:
    """
    通过 SauceNAO 检索插画/图片来源信息。
    优先使用 POST 上传图片字节流。若提供 api_key，优先解析 JSON 接口；否则解析 HTML 页面。
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
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. 尝试将 URL 转为本地字节流
            if image_url and not image_bytes:
                clean_path = (
                    image_url[7:] if image_url.startswith("file://") else image_url
                )
                if os.path.exists(clean_path):
                    try:
                        image_bytes = Path(clean_path).read_bytes()
                    except Exception as e:
                        logger.error(f"[Giftia] 读取本地插画图片失败 {clean_path}: {e}")
                elif image_url.startswith(("http://", "https://")):
                    try:
                        async with session.get(image_url) as img_resp:
                            if img_resp.status == 200:
                                image_bytes = await img_resp.read()
                    except Exception as e:
                        logger.warning(f"[Giftia] 本地拉取插画图片 URL 失败: {e}")

            form = aiohttp.FormData()
            if image_bytes:
                form.add_field(
                    "file", image_bytes, filename="search.jpg", content_type="image/jpeg"
                )
            elif image_url and image_url.startswith(("http://", "https://")):
                form.add_field("url", image_url)
            else:
                return (
                    False,
                    MessageChain([Plain("缺少有效的插画图片数据")]),
                    "No image provided",
                )

            form.add_field("db", "999")
            max_count = max(1, min(int(limit or 3), 10))
            form.add_field("numres", str(max_count))
            if api_key:
                form.add_field("output_type", "2")
                form.add_field("api_key", api_key)

            async with session.post("https://saucenao.com/search.php", data=form) as resp:
                if resp.status != 200:
                    return (
                        False,
                        MessageChain([Plain(f"SauceNAO 请求失败 (HTTP {resp.status})")]),
                        f"HTTP {resp.status}",
                    )

                if api_key:
                    res_json = await resp.json()
                    return _parse_saucenao_json(res_json, max_count, bot_id, bot_name)
                else:
                    html_text = await resp.text()
                    return _parse_saucenao_html(html_text, max_count, bot_id, bot_name)

    except Exception as e:
        logger.error(f"[Giftia] SauceNAO 搜图异常: {e}", exc_info=True)
        return False, MessageChain([Plain(f"SauceNAO 搜图发生错误: {e}")]), str(e)


def _parse_saucenao_html(
    html_text: str, limit: int, bot_id: str, bot_name: str
) -> tuple[bool, MessageChain, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    results = []

    for res in soup.find_all("div", class_="result"):
        if res.get("id") == "result-hidden-notification":
            continue
        sim_elem = res.find("div", class_="resultsimilarityinfo")
        if not sim_elem:
            continue
        sim_str = sim_elem.get_text(strip=True)

        # 提取缩略图
        img_url = ""
        img_elem = res.find("div", class_="resultimage")
        if img_elem:
            img_tag = img_elem.find("img")
            if img_tag:
                raw_url = (
                    img_tag.get("src")
                    or img_tag.get("data-src")
                    or img_tag.get("data-src2")
                    or ""
                )
                if raw_url:
                    img_url = urllib.parse.urljoin("https://saucenao.com/", raw_url)

        # 标题
        title_elem = res.find("div", class_="resulttitle")
        title = title_elem.get_text(strip=True) if title_elem else "未知作品"

        # 详细列信息与链接
        content_cols = res.find_all("div", class_="resultcontentcolumn")
        details = []
        source_links = []
        for col in content_cols:
            text = col.get_text(" ", strip=True)
            if text:
                details.append(text)
            for a in col.find_all("a"):
                href = a.get("href", "")
                if href and href.startswith("http") and "saucenao.com/info.php" not in href:
                    label = a.get_text(strip=True)
                    source_links.append(f"{label}: {href}")

        results.append({
            "similarity": sim_str,
            "title": title,
            "img_url": img_url,
            "details": details,
            "links": source_links,
        })

    if not results:
        return (
            False,
            MessageChain([Plain("🧐 没有识别到相关插画来源信息的喵")]),
            "No results found",
        )

    target_results = results[:limit]
    nodes = []
    uploader_uin = bot_id or "10000"
    uploader_name = bot_name or "Giftia"

    header_node = Node(
        uin=uploader_uin,
        name=uploader_name,
        content=[
            Plain(
                f"🎨 SauceNAO 插画来源结果\n"
                f"共匹配到 {len(target_results)} 个出处："
            )
        ],
    )
    nodes.append(header_node)

    for idx, item in enumerate(target_results, 1):
        lines = [f"【结果 #{idx}】"]
        if item["title"]:
            lines.append(f"作品: {item['title']}")
        lines.append(f"相似度: {item['similarity']}")

        if item["details"]:
            lines.append("信息: " + " | ".join(item["details"]))

        if item["links"]:
            lines.append("链接: " + " ; ".join(item["links"][:3]))

        node_content = [Plain("\n".join(lines))]
        if item["img_url"] and item["img_url"].startswith(("http://", "https://")):
            node_content.append(Image.fromURL(item["img_url"]))

        nodes.append(
            Node(
                uin=uploader_uin,
                name=f"插画来源 #{idx} | {item['similarity']}",
                content=node_content,
            )
        )

    return True, MessageChain([Nodes(nodes)]), ""


def _parse_saucenao_json(
    data: dict, limit: int, bot_id: str, bot_name: str
) -> tuple[bool, MessageChain, str]:
    results = data.get("results") or []
    if not results:
        return (
            False,
            MessageChain([Plain("🧐 没有识别到相关插画来源信息的喵")]),
            "No results found",
        )

    target_results = results[:limit]
    nodes = []
    uploader_uin = bot_id or "10000"
    uploader_name = bot_name or "Giftia"

    header_node = Node(
        uin=uploader_uin,
        name=uploader_name,
        content=[
            Plain(
                f"🎨 SauceNAO 插画来源结果 (API)\n"
                f"共匹配到 {len(target_results)} 个出处："
            )
        ],
    )
    nodes.append(header_node)

    for idx, item in enumerate(target_results, 1):
        header = item.get("header") or {}
        item_data = item.get("data") or {}

        similarity = header.get("similarity", "0")
        thumbnail = header.get("thumbnail", "")

        title = (
            item_data.get("title")
            or item_data.get("source")
            or item_data.get("material")
            or "未知作品"
        )
        author = (
            item_data.get("author_name")
            or item_data.get("member_name")
            or item_data.get("creator")
            or ""
        )
        pixiv_id = item_data.get("pixiv_id") or ""
        ext_urls = item_data.get("ext_urls") or []

        lines = [f"【结果 #{idx}】", f"作品/来源: {title}", f"相似度: {similarity}%"]
        if author:
            lines.append(f"作者/画师: {author}")
        if pixiv_id:
            lines.append(f"Pixiv ID: {pixiv_id}")
        if ext_urls:
            lines.append("链接: " + " ; ".join(ext_urls[:3]))

        node_content = [Plain("\n".join(lines))]
        if thumbnail:
            node_content.append(Image.fromURL(thumbnail))

        nodes.append(
            Node(
                uin=uploader_uin,
                name=f"插画来源 #{idx} | {similarity}%",
                content=node_content,
            )
        )

    return True, MessageChain([Nodes(nodes)]), ""


async def search_illust_by_media_id(
    plugin,
    media_id: str,
    limit: int = 3,
    api_key: str = "",
    bot_id: str = "",
    bot_name: str = "Giftia",
) -> tuple[bool, MessageChain, str]:
    """
    根据 media_id (或 hash_val) 查找图片数据并执行 SauceNAO 搜插画。
    """
    cache_file = (
        StarTools.get_data_dir("astrbot_plugin_giftia")
        / "media_cache"
        / media_id
    )
    if cache_file.exists():
        try:
            image_bytes = cache_file.read_bytes()
            return await search_illust_by_image(
                image_bytes=image_bytes,
                limit=limit,
                api_key=api_key,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        except Exception as e:
            logger.error(f"[Giftia] 读取媒体缓存 {media_id} 失败: {e}")

    media_caption = await plugin.data_cache.get_caption_by_hash(media_id)
    if media_caption and media_caption.url:
        return await search_illust_by_image(
            image_url=media_caption.url,
            limit=limit,
            api_key=api_key,
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
