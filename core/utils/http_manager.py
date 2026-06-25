import base64
import ssl
from datetime import datetime
from io import BytesIO
from pathlib import Path

from aiohttp import (
    ClientConnectorCertificateError,
    ClientConnectorSSLError,
    ClientSession,
    ClientTimeout,
)
from PIL import Image

from astrbot.api import logger
from astrbot.core import AstrBotConfig


class HttpManager:
    def __init__(self, config: AstrBotConfig):
        self.session = ClientSession(timeout=ClientTimeout(connect=30, total=60))
        self.config = config

    async def download_media(self, url: str) -> bytes:
        """下载媒体文件"""
        # 如果是本地文件路径，直接从本地读取
        if not url.startswith("http://") and not url.startswith("https://"):
            local_path = url
            if local_path.startswith("file://"):
                local_path = local_path[7:]
            # Windows system path handling: file:///C:/path -> C:/path
            if (
                local_path.startswith("/")
                and len(local_path) > 2
                and local_path[2] == ":"
            ):
                local_path = local_path[1:]
            path = Path(local_path)
            try:
                if path.exists():
                    with open(path, "rb") as f:
                        return f.read()
                else:
                    logger.error(f"本地媒体文件不存在: {path}")
            except Exception as e:
                logger.error(f"从本地读取媒体文件失败: {e}, Path: {path}")
            return b""

        for _ in range(3):
            try:
                headers = {"Referer": "https://im.qq.com/"}
                async with self.session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    else:
                        logger.error(f"下载媒体文件失败: {resp.status}, URL: {url}")
            except (
                ClientConnectorSSLError,
                ClientConnectorCertificateError,
            ):
                logger.warning(
                    f"SSL 证书验证失败，将尝试临时关闭 SSL 验证重新下载: {url}"
                )
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                async with self.session.get(
                    url, ssl=ssl_context, headers=headers
                ) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    else:
                        logger.error(
                            f"下载媒体文件失败: {resp.status}，retry: {_ + 1} times, URL: {url}"
                        )
            except Exception as e:
                logger.error(f"下载媒体文件失败: {e}，retry: {_ + 1} times")
        return b""

    @staticmethod
    def handle_image(image_bytes: bytes, max_frames: int = 8) -> tuple[list[str], bool]:
        try:
            results = []
            with Image.open(BytesIO(image_bytes)) as img:
                is_animated = getattr(img, "is_animated", False)
                if is_animated:
                    total_frames = getattr(img, "n_frames", 1)
                    if total_frames <= max_frames:
                        frame_indices = list(range(total_frames))
                    else:
                        frame_indices = [
                            int(i * (total_frames - 1) / (max_frames - 1))
                            for i in range(max_frames)
                        ]
                    for idx in frame_indices:
                        img.seek(idx)
                        # 使用副本进行转换，不破坏原 img 对象的帧索引
                        frame = img.convert("RGB")
                        buf = BytesIO()
                        frame.save(buf, format="JPEG", quality=90)
                        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                        results.append("base64://" + b64)
                else:
                    frame = img.convert("RGB")
                    buf = BytesIO()
                    frame.save(buf, format="JPEG", quality=90)
                    results.append(
                        "base64://" + base64.b64encode(buf.getvalue()).decode("utf-8")
                    )

                return results, is_animated
        except Exception as e:
            logger.warning(f"图片处理失败: {e}")
            return [], False

    @staticmethod
    def handle_audio(audio_bytes: bytes) -> list[str]:
        """处理语音"""
        try:
            results = []
            with open("temp.silk", "wb") as f:
                f.write(audio_bytes)
            return results
        except Exception as e:
            logger.warning(f"语音处理失败: {e}")
            return []

    async def upload_file(self, file_path: Path) -> bool:
        """上传文件到R2"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            remote_file_name = f"{timestamp}_giftia.sqlite"
            with open(file_path, "rb") as f:
                async with self.session.put(
                    f"{self.config.get('r2_config', {}).get('r2_base_url', '')}/{remote_file_name}",
                    data=f,
                    headers={
                        "X-Auth-Token": self.config.get("r2_config", {}).get(
                            "r2_auth_token", ""
                        ),
                        "Content-Type": "application/x-sqlite3",
                    },
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"文件成功备份至 R2: {remote_file_name}")
                        return True
                    else:
                        body = await resp.text()
                        logger.error(f"R2 响应错误 (状态码 {resp.status}): {body}")
                        return False
        except Exception as e:
            logger.error(f"备份文件上传到R2失败: {e}")
            return False

    async def close_session(self) -> None:
        """关闭客户端会话"""
        if self.session and not self.session.closed:
            await self.session.close()
