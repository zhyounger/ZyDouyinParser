import tomllib
import os
import re
import aiohttp
import ssl
from typing import Dict, Any

from utils.plugin_base import PluginBase
from WechatAPI.Client import WechatAPIClient
from utils.decorators import on_text_message
from loguru import logger


class VideoParserError(Exception):
    pass


class ZyDouyiParser(PluginBase):
    description = "抖音解析插件，更多资源，关注公众号：Young宝库"
    author = "zhyoung"
    version = "1.0.0"
    name = "抖音解析"

    def __init__(self):
        super().__init__()
        # 加载配置文件
        self.load_config()

    def load_config(self):
        with open("plugins/Zy-douyin/config.toml", "rb") as f:
            config = tomllib.load(f)

        config = config["ZyDouyinParser"]
        self.enable = config["enable"]
        self.allowed_groups = config["allowed_groups"]

    @on_text_message(priority=10)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        content = message["Content"].strip()
        group_id = message["FromWxid"]

        # 检查群聊白名单
        if "*" not in self.allowed_groups and group_id not in self.allowed_groups:
            return

        # 检查是否包含抖音分享内容
        douyin_url = None
        # 匹配抖音分享文本特征
        share_pattern = r"复制打开抖音|打开抖音|抖音视频"
        url_pattern = r'https?://[^\s<>"]+?(?:douyin\.com|iesdouyin\.com)[^\s<>"]*'

        if re.search(share_pattern, content) or re.search(url_pattern, content):
            # 提取抖音链接
            match = re.search(url_pattern, content)
            if match:
                douyin_url = match.group(0)

        if douyin_url:
            try:
                # 直接调用本地解析逻辑
                result = await self.parse_video(douyin_url)
                logger.debug(f"抖音解析结果: {result}")
                # 组装卡片消息
                # await self._send_video_card(bot, group_id, result)
                await bot.send_text_message(group_id, result)

            except VideoParserError as e:
                logger.error(f"解析抖音视频失败: {str(e)}")
                await bot.send_text_message(group_id, f"解析失败: {str(e)}")
            except Exception as e:
                logger.error(f"处理抖音链接时发生错误: {str(e)}")
                await bot.send_text_message(group_id, "解析失败，请稍后重试")

    # 解析视频链接
    async def parse_video(self, video_url: str) -> Dict[str, Any]:
        """解析视频链接"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
            }

            # 获取重定向后的真实链接
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(video_url, allow_redirects=False) as response:
                    if response.status == 302:
                        video_url = response.headers.get("Location")

                # 获取页面内容
                async with session.get(video_url, headers=headers) as response:
                    if response.status != 200:
                        raise VideoParserError(
                            f"获取页面失败，状态码：{response.status}"
                        )

                    html_content = await response.text()
                    if not html_content:
                        raise VideoParserError("页面内容为空")

                    # 合并后的正则表达式
                    pattern = re.compile(
                        r'"play_addr":\s*{\s*"uri":\s*"[^"]*",\s*"url_list":\s*\[([^\]]*)\]'
                    )
                    match = pattern.search(html_content)

                    if not match:
                        raise VideoParserError("未找到视频链接")

                    url_list_str = match.group(1)
                    urls = [url.strip().strip('"') for url in url_list_str.split(",")]

                    if not urls:
                        raise VideoParserError("视频链接列表为空")

                    # 解码并处理所有URL
                    decoded_urls = [
                        url.strip()
                        .strip('"')
                        .encode()
                        .decode("unicode-escape")
                        .replace("playwm", "play")
                        for url in urls
                    ]

                    # 优先选择aweme.snssdk.com域名的链接
                    snssdk_urls = [
                        url for url in decoded_urls if "aweme.snssdk.com" in url
                    ]
                    if not snssdk_urls:
                        raise VideoParserError("未找到有效的视频源链接")

                    video_url = snssdk_urls[0]

                    # 处理重定向，确保获取最终的视频地址
                    max_redirects = 3
                    redirect_count = 0

                    while redirect_count < max_redirects:
                        async with session.get(
                            video_url, headers=headers, allow_redirects=False
                        ) as response:
                            if response.status == 302:
                                new_url = response.headers.get("Location")
                                if "aweme.snssdk.com" in new_url:
                                    video_url = new_url
                                    redirect_count += 1
                                else:
                                    break
                            else:
                                break

                    if not video_url:
                        raise VideoParserError("无法获取有效的视频地址")

                    # 提取标题等信息
                    title_pattern = re.compile(r'"desc":\s*"([^"]+)"')
                    author_pattern = re.compile(r'"nickname":\s*"([^"]+)"')
                    cover_pattern = re.compile(
                        r'"cover":\s*{\s*"url_list":\s*\[\s*"([^"]+)"\s*\]\s*}'
                    )

                    title_match = title_pattern.search(html_content)
                    author_match = author_pattern.search(html_content)
                    cover_match = cover_pattern.search(html_content)

                    return {
                        "url": video_url,
                        "title": title_match.group(1) if title_match else "",
                        "author": author_match.group(1) if author_match else "",
                        "cover": cover_match.group(1) if cover_match else "",
                    }

        except aiohttp.ClientError as e:
            raise VideoParserError(f"网络请求失败：{str(e)}")
        except Exception as e:
            raise VideoParserError(f"解析过程发生错误：{str(e)}")
