import asyncio
import subprocess
import base64
import shutil
import binascii
from database.XYBotDB import XYBotDB

import tomllib
import os
import re
import aiohttp
import ssl
from typing import Dict, Any, Optional

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
        self.db = XYBotDB()
        # 加载配置文件
        self.load_config()


    def load_config(self):
        with open("plugins/ZyDouyinParser/config.toml", "rb") as f:
            config = tomllib.load(f)

        config = config["ZyDouyinParser"]
        self.enable = config["enable"]
        self.allowed_groups = config["allowed_groups"]
        self.ffmpeg_path = config.get("ffmpeg_path", "/usr/bin/ffmpeg")  # ffmpeg 路径
        self.video_sources = config.get("video_sources", [])  # 视频源列表

        logger.info("ZyDouyinParser 插件配置加载成功")
        self.ffmpeg_available = self._check_ffmpeg()  # 在配置加载完成后检查 ffmpeg

    def _check_ffmpeg(self) -> bool:
        """检查 ffmpeg 是否可用"""
        try:
            process = subprocess.run(
                [self.ffmpeg_path, "-version"], check=False, capture_output=True
            )
            if process.returncode == 0:
                logger.info(f"ffmpeg 可用，版本信息：{process.stdout.decode()}")
                return True
            else:
                logger.warning(
                    f"ffmpeg 执行失败，返回码: {process.returncode}，错误信息: {process.stderr.decode()}"
                )
                return False
        except FileNotFoundError:
            logger.warning(f"ffmpeg 未找到，路径: {self.ffmpeg_path}")
            return False
        except Exception as e:
            logger.exception(f"检查 ffmpeg 失败: {e}")
            return False


    async def _download_video(self, video_url: str) -> bytes:
        """下载视频文件"""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.get(video_url) as response:
                    if response.status == 200:
                        video_data = await response.read()
                        logger.debug(f"下载的视频数据大小: {len(video_data)} bytes")
                        return video_data
                    else:
                        logger.error(f"下载视频失败，状态码: {response.status}")
                        return b""  # 返回空字节
        except Exception as e:
            logger.exception(f"下载视频过程中发生异常: {e}")
            return b""  # 返回空字节

    async def _extract_thumbnail_from_video(self, video_data: bytes) -> Optional[str]:
        """从视频数据中提取缩略图"""
        temp_dir = "temp_videos"  # 创建临时文件夹
        os.makedirs(temp_dir, exist_ok=True)
        video_path = os.path.join(temp_dir, "temp_video.mp4")
        thumbnail_path = os.path.join(temp_dir, "temp_thumbnail.jpg")

        try:
            with open(video_path, "wb") as f:
                f.write(video_data)

            # 异步执行 ffmpeg 命令
            process = await asyncio.create_subprocess_exec(
                self.ffmpeg_path,
                "-i",
                video_path,
                "-ss",
                "00:00:01",  # 从视频的第 1 秒开始提取
                "-vframes",
                "1",
                thumbnail_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"ffmpeg 执行失败: {stderr.decode()}")
                return None

            with open(thumbnail_path, "rb") as image_file:
                image_data = image_file.read()
                image_base64 = base64.b64encode(image_data).decode("utf-8")
                return image_base64

        except FileNotFoundError:
            logger.error("ffmpeg 未找到，无法提取缩略图")
            return None
        except Exception as e:
            logger.exception(f"提取缩略图失败: {e}")
            return None
        finally:
            # 清理临时文件
            shutil.rmtree(temp_dir, ignore_errors=True)  # 递归删除临时文件夹



    @on_text_message(priority=10)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return
        query_wxid = message["SenderWxid"]
        
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
                # 发送视频源链接
                # await bot.send_text_message(group_id, "原始链接为：" + result)
                if result:
                    logger.info(f"获取到视频链接: {result}")
                    video_data = await self._download_video(result)

                    if video_data:
                        image_base64 = None
                        if self.ffmpeg_available:
                            # 获取缩略图
                            image_base64 = await self._extract_thumbnail_from_video(
                                video_data
                            )

                            if image_base64:
                                logger.info("成功提取缩略图")
                            else:
                                logger.warning("未能成功提取缩略图")
                        else:
                            await bot.send_text_message(
                                group_id, "由于 ffmpeg 未安装，无法提取缩略图。"
                            )

                        try:
                            video_base64 = base64.b64encode(video_data).decode("utf-8")
                            logger.debug(
                                f"视频 Base64 长度: {len(video_base64) if video_base64 else '无效'}"
                            )
                            logger.debug(
                                f"图片 Base64 长度: {len(image_base64) if image_base64 else '无效'}"
                            )

                            # 发送视频消息
                            await bot.send_video_message(
                                group_id,
                                video=video_base64,
                                image=image_base64 or "None",
                            )
                            self.db.add_points(query_wxid, -5)
                            await bot.send_text_message(
                                group_id, "视频解析成功，扣减5积分。"
                            )
                            logger.info(f"成功发送视频到 {group_id}")

                        except binascii.Error as e:
                            logger.error(f"Base64 编码失败： {e}")
                            await bot.send_text_message(
                                group_id, "视频编码失败，请稍后重试。"
                            )

                        except Exception as e:
                            logger.exception(f"发送视频过程中发生异常: {e}")
                            await bot.send_text_message(
                                group_id, f"发送视频过程中发生异常，请稍后重试: {e}"
                            )

                    else:
                        logger.warning(f"未能下载到有效的视频数据")
                        await bot.send_text_message(
                            group_id, "未能下载到有效的视频，请稍后重试。"
                        )

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

                    # return {
                    #     "url": video_url,
                    #     "title": title_match.group(1) if title_match else "",
                    #     "author": author_match.group(1) if author_match else "",
                    #     "cover": cover_match.group(1) if cover_match else "",
                    # }
                    return video_url

        except aiohttp.ClientError as e:
            raise VideoParserError(f"网络请求失败：{str(e)}")
        except Exception as e:
            raise VideoParserError(f"解析过程发生错误：{str(e)}")
