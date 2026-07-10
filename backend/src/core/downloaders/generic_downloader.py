"""
通用下载器
作为后备方案，支持所有 yt-dlp 支持的平台
"""
import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from pathlib import Path
import yt_dlp
from src.core.tool_manager import get_tool_manager
from src.core.cookie_storage import cookiefile_for_ytdlp
from .base_downloader import BaseDownloader
from .proxy_config import get_ydl_proxy_opts

logger = logging.getLogger(__name__)


class GenericDownloader(BaseDownloader):
    """通用下载器，支持所有yt-dlp支持的平台"""

    def __init__(self, output_dir: str = None):
        super().__init__(output_dir)
        self.platform_name = "generic"
        # 智能回退模式下是否使用 Cookie（由 DownloaderFactory 设置）
        self._use_cookie_in_smart_mode = True

    @staticmethod
    def supports_url(url: str) -> bool:
        """通用下载器支持所有URL"""
        return True

    def _convert_vimeo_url(self, url: str) -> str:
        """
        将 vimeo.com/xxx 格式的 URL 转换为 player.vimeo.com/video/xxx 格式
        这样可以绕过 Vimeo 的登录要求
        """
        import re
        # 匹配 vimeo.com/数字 格式（不是 player.vimeo.com）
        match = re.match(r'https?://(?:www\.)?vimeo\.com/(\d+)', url)
        if match:
            video_id = match.group(1)
            converted_url = f'https://player.vimeo.com/video/{video_id}'
            logger.info(f"Converted Vimeo URL: {url} -> {converted_url}")
            return converted_url
        return url

    async def get_video_info(self, url: str) -> Dict[str, Any]:
        """获取视频信息"""
        try:
            # Vimeo URL 转换（绕过登录要求）
            original_url = url
            if 'vimeo.com' in url.lower() and 'player.vimeo.com' not in url.lower():
                url = self._convert_vimeo_url(url)

            # 检查缓存
            cached_info = self._get_cached_info(url)
            if cached_info:
                logger.debug(f"Using cached info for: {url}")
                return cached_info

            # 检测平台以优化配置
            platform = self._detect_platform(url)

            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                # 通用反爬配置
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en-US,en;q=0.8',
                    # 注意：不要设置 Accept-Encoding，让 yt-dlp 自动处理压缩
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                },
                # 超时配置
                'socket_timeout': 30,
                # 代理配置
                **get_ydl_proxy_opts(),
            }

            # YouTube 特殊处理：使用不需要 PO Token 的客户端
            if platform == 'youtube':
                ydl_opts['extractor_args'] = {
                    'youtube': {
                        'player_client': ['android_sdkless', 'web_safari'],
                    }
                }

            # 针对国内平台优化
            if platform in ['xiaohongshu', 'xinpianchang', 'douyin', 'weixin', 'tencent', 'youku', 'iqiyi', 'bilibili']:
                ydl_opts['geo_bypass'] = True
                ydl_opts['geo_bypass_country'] = 'CN'
                # 添加 Referer 避免403
                if platform == 'xiaohongshu':
                    ydl_opts['http_headers']['Referer'] = 'https://www.xiaohongshu.com/'
                elif platform == 'xinpianchang':
                    ydl_opts['http_headers']['Referer'] = url.split('?')[0]
                elif platform == 'bilibili':
                    ydl_opts['http_headers']['Referer'] = 'https://www.bilibili.com/'
                elif platform in ['tencent', 'youku', 'iqiyi']:
                    # 国内视频平台通用 Referer
                    ydl_opts['http_headers']['Referer'] = url.split('?')[0]  # 使用当前页面作为 Referer

            # Vimeo 优化
            if platform == 'vimeo':
                ydl_opts['http_headers']['Referer'] = 'https://vimeo.com/'
                ydl_opts['socket_timeout'] = 60  # Vimeo 需要更长超时

            # 为特定平台添加 Cookie 支持（仅在非智能回退模式或允许使用 Cookie 时）
            cookie_path = None
            if getattr(self, '_use_cookie_in_smart_mode', True):
                cookie_path = self._get_platform_cookie_path(url)

            loop = asyncio.get_event_loop()

            def _extract_info():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            with cookiefile_for_ytdlp(cookie_path) as ytdlp_cookie_path:
                if ytdlp_cookie_path:
                    ydl_opts['cookiefile'] = str(ytdlp_cookie_path)
                    logger.info(f"Using cookies from: {cookie_path}")

                info = await loop.run_in_executor(None, _extract_info)

            # 提取通用信息
            video_info = {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'description': info.get('description', ''),
                'uploader': info.get('uploader', 'Unknown'),
                'upload_date': info.get('upload_date', ''),
                'view_count': info.get('view_count', 0),
                'platform': self._detect_platform(url),
                'formats': self._extract_formats(info),
                'url': url
            }

            # 缓存结果
            self._cache_info(url, video_info)

            logger.info(f"Successfully extracted info: {video_info['title']}")
            return video_info

        except Exception as e:
            logger.error(f"Error extracting video info: {e}")
            raise Exception(f"Failed to get video info: {str(e)}")

    async def download_video(
        self,
        url: str,
        quality: str = "best",
        output_path: Optional[str] = None,
        format_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        task_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """下载视频"""
        try:
            # Vimeo URL 转换（绕过登录要求）
            if 'vimeo.com' in url.lower() and 'player.vimeo.com' not in url.lower():
                url = self._convert_vimeo_url(url)

            output_path = output_path or str(self.output_dir)

            output_format: Optional[str] = None
            if format_id:
                fid = str(format_id).strip().lower()
                if fid in ('mp4', 'mkv', 'webm', 'mp3'):
                    output_format = fid

            format_quality = 'audio' if output_format == 'mp3' else quality
            merge_format = output_format if output_format in ('mp4', 'mkv', 'webm') else 'mp4'

            # 检测平台以优化配置
            platform = self._detect_platform(url)

            # 通用下载选项
            ydl_opts = {
                'format': self._get_format_selector(format_quality, format_id),
                'outtmpl': f'{output_path}/%(title)s_{quality}.%(ext)s',
                'quiet': False,
                'no_warnings': False,
                'progress_hooks': [],
                'merge_output_format': merge_format,
                # 通用反爬配置
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en-US,en;q=0.8',
                    # 注意：不要设置 Accept-Encoding，让 yt-dlp 自动处理压缩
                    'DNT': '1',
                    'Connection': 'keep-alive',
                },
                # 网络优化
                'socket_timeout': 30,
                'retries': 10,
                'fragment_retries': 10,
                'skip_unavailable_fragments': True,
                'concurrent_fragment_downloads': 3,
                'http_chunk_size': 10485760,
                # 代理配置
                **get_ydl_proxy_opts(),
            }

            tool_mgr = get_tool_manager()
            ffmpeg_path = tool_mgr.get_ffmpeg_path()
            if ffmpeg_path:
                ydl_opts['ffmpeg_location'] = str(Path(ffmpeg_path).parent)

            if output_format == 'mp3':
                ydl_opts.pop('merge_output_format', None)
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '0',
                }]

            # 针对国内平台优化
            if platform in ['xiaohongshu', 'xinpianchang', 'douyin', 'weixin', 'tencent', 'youku', 'iqiyi', 'bilibili']:
                ydl_opts['geo_bypass'] = True
                ydl_opts['geo_bypass_country'] = 'CN'
                # 添加 Referer 避免403
                if platform == 'xiaohongshu':
                    ydl_opts['http_headers']['Referer'] = 'https://www.xiaohongshu.com/'
                elif platform == 'xinpianchang':
                    ydl_opts['http_headers']['Referer'] = url.split('?')[0]
                elif platform == 'bilibili':
                    ydl_opts['http_headers']['Referer'] = 'https://www.bilibili.com/'
                elif platform in ['tencent', 'youku', 'iqiyi']:
                    ydl_opts['http_headers']['Referer'] = url.split('?')[0]

            # Vimeo 优化
            if platform == 'vimeo':
                ydl_opts['http_headers']['Referer'] = 'https://vimeo.com/'
                ydl_opts['socket_timeout'] = 60

            # YouTube 特殊处理：排除 HLS 格式（SABR 限制导致 403）
            if platform == 'youtube':
                # 🔥 YouTube SABR 限制：HLS 片段会 403，必须排除
                original_format = ydl_opts['format']

                # 智能修改格式选择器，为每个部分添加协议过滤
                if '[protocol' not in original_format:
                    filtered_parts = []
                    for part in original_format.split('/'):
                        # 只为 video/audio 选择器添加过滤
                        if 'video' in part or 'audio' in part:
                            # 在现有过滤器后添加协议过滤
                            if '[' in part and ']' in part:
                                # 已有过滤器，在第一个 ] 后插入
                                part = part.replace(']', '][protocol!*=m3u8][protocol!*=dash]', 1)
                            elif 'video' in part or 'audio' in part:
                                # 没有过滤器，直接添加
                                part = part + '[protocol!*=m3u8][protocol!*=dash]'
                        filtered_parts.append(part)
                    ydl_opts['format'] = '/'.join(filtered_parts)
                    logger.info(f"[YouTube] Modified format to exclude HLS/DASH: {ydl_opts['format'][:100]}...")

                # 使用稳定的客户端
                ydl_opts['extractor_args'] = {
                    'youtube': {
                        'player_client': ['android_sdkless'],  # 最稳定的客户端
                    }
                }
                # 优化下载配置
                ydl_opts['format_sort'] = ['proto:https', 'proto:http', 'vcodec:h264', 'acodec:aac']
                ydl_opts['concurrent_fragment_downloads'] = 1  # 单线程，避免限流
                ydl_opts['retries'] = 10
                ydl_opts['fragment_retries'] = 10

            # 为特定平台添加 Cookie 支持（仅在非智能回退模式或允许使用 Cookie 时）
            cookie_path = None
            if getattr(self, '_use_cookie_in_smart_mode', True):
                cookie_path = self._get_platform_cookie_path(url)

            # 取消检查标志（用于在 progress_hook 中检查）
            cancel_checker = None
            if task_id:
                try:
                    from src.core.download_queue import get_download_queue
                    cancel_checker = get_download_queue()
                except Exception:
                    pass

            # 添加进度钩子
            if progress_callback:
                # 获取当前事件循环供 progress_hook 使用
                loop = asyncio.get_event_loop()

                # 用于跟踪多分片下载的累积进度
                progress_state = {
                    'total_downloaded': 0,  # 累积已下载字节
                    'estimated_total': 0,   # 估算总大小
                    'last_progress': 0,     # 上次进度（用于防止回退）
                    'fragment_index': 0,    # 当前分片索引
                    'fragment_count': 0,    # 总分片数
                }

                def progress_hook(d):
                    # 检查任务是否被取消
                    if cancel_checker and task_id and cancel_checker.is_task_cancelled_sync(task_id):
                        logger.info(f"Task {task_id} cancelled, raising exception to stop download")
                        raise Exception(f"Download cancelled by user")

                    if d['status'] == 'downloading':
                        try:
                            downloaded = d.get('downloaded_bytes', 0)
                            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                            speed = d.get('speed', 0)
                            eta = d.get('eta', 0)

                            # 获取分片信息（用于 HLS/DASH 流）
                            fragment_index = d.get('fragment_index', 0)
                            fragment_count = d.get('fragment_count', 0)

                            # 计算进度
                            if fragment_count > 0:
                                # 多分片下载：基于分片索引计算进度
                                # 当前分片进度 = downloaded / total (0-1)
                                # 总进度 = (fragment_index + 当前分片进度) / fragment_count
                                fragment_progress = (downloaded / total) if total > 0 else 0
                                progress = ((fragment_index + fragment_progress) / fragment_count) * 100

                                # 更新状态
                                progress_state['fragment_index'] = fragment_index
                                progress_state['fragment_count'] = fragment_count

                                # 估算总大小（基于已下载分片的平均大小）
                                if fragment_index > 0:
                                    avg_fragment_size = (progress_state['total_downloaded'] + downloaded) / (fragment_index + fragment_progress)
                                    progress_state['estimated_total'] = int(avg_fragment_size * fragment_count)

                                # 累积已下载（当分片完成时更新）
                                if fragment_progress >= 0.99:
                                    progress_state['total_downloaded'] += total
                            elif total > 0:
                                # 单文件下载：直接计算进度
                                progress = (downloaded / total) * 100
                                progress_state['estimated_total'] = total
                            else:
                                # 无法计算进度
                                progress = progress_state['last_progress']

                            # 防止进度回退（只允许向前）
                            progress = max(progress, progress_state['last_progress'])
                            progress_state['last_progress'] = progress

                            # 使用估算的总大小
                            display_total = progress_state['estimated_total'] if progress_state['estimated_total'] > 0 else total
                            display_downloaded = int(display_total * progress / 100) if display_total > 0 else downloaded

                            # 使用 run_coroutine_threadsafe 从非事件循环线程调度协程
                            asyncio.run_coroutine_threadsafe(
                                progress_callback({
                                    'task_id': task_id,
                                    'status': 'downloading',
                                    'progress': round(progress, 1),
                                    'downloaded': display_downloaded,
                                    'total': display_total,
                                    'speed': speed,
                                    'eta': eta
                                }),
                                loop
                            )
                        except Exception as e:
                            if "cancelled" in str(e).lower():
                                raise  # 重新抛出取消异常
                            logger.error(f"Progress callback error: {e}")

                    elif d['status'] == 'finished':
                        asyncio.run_coroutine_threadsafe(
                            progress_callback({
                                'task_id': task_id,
                                'status': 'finished',
                                'filename': d.get('filename', '')
                            }),
                            loop
                        )

                ydl_opts['progress_hooks'].append(progress_hook)

            # 执行下载
            loop = asyncio.get_event_loop()

            def _download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return {
                        'title': info.get('title', 'Unknown'),
                        'filename': ydl.prepare_filename(info),
                        'duration': info.get('duration', 0),
                        'filesize': info.get('filesize', 0),
                    }

            with cookiefile_for_ytdlp(cookie_path) as ytdlp_cookie_path:
                if ytdlp_cookie_path:
                    ydl_opts['cookiefile'] = str(ytdlp_cookie_path)
                    logger.info(f"Using cookies for download from: {cookie_path}")

                result = await loop.run_in_executor(None, _download)

            logger.info(f"Successfully downloaded: {result['title']}")

            return {
                'status': 'success',
                'title': result['title'],
                'filename': result['filename'],
                'duration': result['duration'],
                'filesize': result['filesize'],
                'platform': self._detect_platform(url),
                'download_time': datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            if progress_callback and task_id:
                await progress_callback({
                    'task_id': task_id,
                    'status': 'error',
                    'error': str(e)
                })
            raise Exception(f"Failed to download video: {str(e)}")

    def _detect_platform(self, url: str) -> str:
        """检测视频平台"""
        url_lower = url.lower()

        if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            return 'youtube'
        elif 'bilibili.com' in url_lower:
            return 'bilibili'
        # ✅ 先检查 TikTok，再检查 Douyin（避免误匹配）
        elif 'tiktok.com' in url_lower:
            return 'tiktok'
        elif 'douyin.com' in url_lower or 'v.douyin.com' in url_lower:
            return 'douyin'
        elif 'twitter.com' in url_lower or 'x.com' in url_lower:
            return 'twitter'
        elif 'instagram.com' in url_lower:
            return 'instagram'
        elif 'facebook.com' in url_lower:
            return 'facebook'
        elif 'xiaohongshu.com' in url_lower:
            return 'xiaohongshu'
        elif 'xinpianchang.com' in url_lower:
            return 'xinpianchang'
        elif 'weixin' in url_lower or 'qq.com/channels' in url_lower:
            return 'weixin'
        elif 'v.qq.com' in url_lower:
            return 'tencent'
        elif 'youku.com' in url_lower:
            return 'youku'
        elif 'iqiyi.com' in url_lower:
            return 'iqiyi'
        elif 'vimeo.com' in url_lower or 'player.vimeo.com' in url_lower:
            return 'vimeo'
        else:
            return 'generic'

    def _get_platform_cookie_path(self, url: str) -> Optional[Path]:
        """
        根据 URL 获取对应平台的 Cookie 文件路径

        Args:
            url: 视频URL

        Returns:
            Cookie 文件路径，如果不存在则返回 None
        """
        # 使用统一的 Cookie 目录获取函数（支持打包环境）
        from .cookie_manager import get_cookie_base_dir
        cookie_dir = get_cookie_base_dir()

        # 根据平台选择 Cookie 文件
        platform = self._detect_platform(url)

        # 使用统一的 Cookie 映射（从 cookie_manager 导入）
        from .cookie_manager import PLATFORM_COOKIE_MAP

        cookie_filename = PLATFORM_COOKIE_MAP.get(platform)
        if not cookie_filename:
            return None

        cookie_file = cookie_dir / cookie_filename
        if cookie_file.exists():
            logger.debug(f"Found cookie file for {platform}: {cookie_file}")
            return cookie_file

        return None
