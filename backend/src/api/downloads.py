"""
下载相关 API 路由
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, field_validator
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
import logging
import re
import uuid
import asyncio
from datetime import datetime

from src.core.downloader import Downloader
from src.core.download_queue import get_download_queue
from src.core.config_manager import get_config_manager
from src.core.websocket_manager import get_ws_manager
from src.models import DownloadTask, get_session
from src.models.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])

# 全局下载器实例
downloader = Downloader()
# 全局队列管理器（从配置读取并发数）
config = get_config_manager()
max_concurrent = config.get('download.max_concurrent', 3)
download_queue = get_download_queue(max_concurrent=max_concurrent)


def _handle_task_exception(task: asyncio.Task):
    """记录后台任务异常，避免静默失败。"""
    try:
        exc = task.exception()
        if exc:
            logger.error(f"Background task failed: {exc}", exc_info=exc)
    except asyncio.CancelledError:
        logger.info("Background task was cancelled")
    except Exception as e:
        logger.error(f"Error handling task exception: {e}", exc_info=True)

# 请求模型
class VideoInfoRequest(BaseModel):
    url: str

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        from urllib.parse import urlparse
        result = urlparse(v)
        if not all([result.scheme, result.netloc]):
            raise ValueError('Invalid URL format')
        return v

class DownloadRequest(BaseModel):
    url: str
    quality: str = "best"
    output_path: Optional[str] = None
    format_id: Optional[str] = None

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        from urllib.parse import urlparse
        result = urlparse(v)
        if not all([result.scheme, result.netloc]):
            raise ValueError('Invalid URL format')
        return v


def _apply_download_result(task: DownloadTask, result_data: dict) -> None:
    task.status = 'completed'
    task.filename = result_data.get('filename')
    task.filesize = result_data.get('filesize')
    actual_quality = str(result_data.get('actual_quality') or '').strip()
    if actual_quality:
        task.quality = actual_quality
    task.completed_at = datetime.now()
    task.progress = 100.0


def _resolve_requested_quality(requested: str, video_info: dict) -> str:
    requested = str(requested or 'best').strip().lower()
    if video_info.get('platform') != 'xinpianchang' or requested in {'best', 'audio'}:
        return requested

    requested_match = re.fullmatch(r'(\d{3,4})p?', requested)
    heights = sorted({
        int(item.get('height'))
        for item in video_info.get('formats') or []
        if item.get('height')
    }, reverse=True)
    if not requested_match or not heights:
        return requested

    requested_height = int(requested_match.group(1))
    eligible = [height for height in heights if height <= requested_height]
    selected_height = eligible[0] if eligible else heights[-1]
    return f'{selected_height}p'

# API 端点
@router.post("/info")
async def get_video_info(request: VideoInfoRequest):
    """
    获取视频信息
    """
    try:
        logger.info(f"Getting video info for: {request.url}")

        # 使用真实的下载器获取信息
        info = await downloader.get_video_info(request.url)

        return {
            "status": "success",
            "data": info
        }
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error(f"Error getting video info: {e}\n{error_trace}")

        # 分析错误类型，提供友好的提示
        error_msg = str(e)
        detail_msg = error_msg

        # 判断是否是国外网站（需要代理）
        foreign_sites = ['youtube.com', 'youtu.be', 'twitter.com', 'x.com', 'instagram.com', 'facebook.com', 'tiktok.com']
        is_foreign_site = any(site in request.url.lower() for site in foreign_sites)

        # 优先检查通用错误（适用于所有平台）

        # 1. Cookie 新鲜度错误（抖音特殊处理）
        if 'Fresh cookies' in error_msg or 'fresh cookies' in error_msg.lower():
            platform_name = "该平台"
            is_douyin = 'douyin.com' in request.url.lower() or 'v.douyin.com' in request.url.lower()

            if is_douyin:
                platform_name = "抖音"
                # 抖音的 "Fresh cookies" 错误通常是反爬机制导致的，不一定是 Cookie 过期
                detail_msg = f"抖音视频获取失败。\n\n💡 可能原因：\n1. 抖音反爬机制限制（最常见）\n2. Cookie 已过期\n\n解决方法：\n1. 稍后重试（等待几分钟）\n2. 如果持续失败，尝试重新获取 Cookie\n3. 使用浏览器直接访问视频页面"
            elif 'tiktok.com' in request.url.lower():
                platform_name = "TikTok"
                detail_msg = f"{platform_name} Cookie 已过期或不够新鲜。\n\n💡 解决方法：\n1. 打开「Cookie 管理」\n2. 重新获取 {platform_name} Cookie（需要重新登录）\n3. 保存后重试"
            elif 'bilibili.com' in request.url.lower():
                platform_name = "B站"
                detail_msg = f"{platform_name} Cookie 已过期或不够新鲜。\n\n💡 解决方法：\n1. 打开「Cookie 管理」\n2. 重新获取 {platform_name} Cookie（需要重新登录）\n3. 保存后重试"
            elif 'xiaohongshu.com' in request.url.lower():
                platform_name = "小红书"
                detail_msg = f"{platform_name} Cookie 已过期或不够新鲜。\n\n💡 解决方法：\n1. 打开「Cookie 管理」\n2. 重新获取 {platform_name} Cookie（需要重新登录）\n3. 保存后重试"
            else:
                detail_msg = f"{platform_name} Cookie 已过期或不够新鲜。\n\n💡 解决方法：\n1. 打开「Cookie 管理」\n2. 重新获取 {platform_name} Cookie（需要重新登录）\n3. 保存后重试"

        # 2. 网络连接错误（可能需要代理）
        elif any(keyword in error_msg.lower() for keyword in ['connection', 'timeout', 'timed out', 'unreachable', 'network']):
            if is_foreign_site:
                detail_msg = f"无法连接到服务器。\n\n💡 原因：该网站可能在国内无法直接访问\n\n解决方法：\n1. 使用代理或 VPN\n2. 在系统设置中配置代理后重试"
            else:
                detail_msg = "网络连接失败。\n\n💡 请检查：\n1. 网络连接是否正常\n2. 防火墙是否阻止了连接"

        # 3. HTTP 403/404 错误
        elif 'HTTP Error 403' in error_msg or 'Forbidden' in error_msg:
            detail_msg = "访问被拒绝（403 Forbidden）。\n\n💡 可能原因：\n1. 需要登录 - 请配置 Cookie\n2. IP 被限制 - 可能需要代理"
            if is_foreign_site:
                detail_msg += "\n3. 需要代理访问该网站"

        elif 'HTTP Error 404' in error_msg:
            detail_msg = "视频不存在或已被删除（404 Not Found）。"

        # 4. 地理限制错误
        elif 'not available' in error_msg.lower() and 'country' in error_msg.lower():
            detail_msg = "该视频在您所在地区不可用（地理限制）。\n\n💡 解决方法：使用代理或 VPN 切换到可用地区"

        # 5. 平台特定错误
        elif 'douyin.com' in request.url.lower() or 'v.douyin.com' in request.url.lower():
            if 'Unable to extract' in error_msg:
                detail_msg = "无法解析抖音视频信息。\n\n💡 可能原因：\n1. 抖音更新了防护机制\n2. Cookie 已过期\n\n建议：重新获取 Cookie 后重试"
            elif 'httpx' in error_msg.lower() or 'module' in error_msg.lower():
                detail_msg = "缺少必要的依赖库。请检查 httpx 是否已安装。"
            else:
                detail_msg = f"获取抖音视频信息失败。\n\n💡 建议：配置抖音 Cookie 可能解决此问题\n\n错误详情：{error_msg}"

        elif 'tiktok.com' in request.url.lower():
            if is_foreign_site and 'connection' not in error_msg.lower():
                detail_msg = f"获取 TikTok 视频信息失败。\n\n💡 提示：TikTok 需要代理访问\n\n错误详情：{error_msg}"
            else:
                detail_msg = f"获取 TikTok 视频信息失败。\n\n错误详情：{error_msg}"

        elif 'bilibili.com' in request.url.lower():
            detail_msg = f"获取 B站 视频信息失败。\n\n💡 部分视频可能需要大会员或登录\n\n错误详情：{error_msg}"

        # 6. YouTube 特殊处理
        elif 'youtube.com' in request.url.lower() or 'youtu.be' in request.url.lower():
            detail_msg = f"获取 YouTube 视频信息失败。\n\n💡 提示：YouTube 在国内需要代理访问\n\n错误详情：{error_msg}"

        # 返回 400 而不是 500，因为这些是用户可操作的错误
        raise HTTPException(status_code=400, detail=detail_msg)

@router.post("/start")
async def start_download(
    request: DownloadRequest,
    db: AsyncSession = Depends(get_session)
):
    """
    开始下载任务
    """
    try:
        logger.info(f"Starting download: {request.url}")

        # 先获取视频信息
        video_info = await downloader.get_video_info(request.url)
        effective_quality = _resolve_requested_quality(request.quality, video_info)

        # 创建任务ID
        task_id = str(uuid.uuid4())

        # 设置默认输出路径
        output_path = request.output_path
        if not output_path:
            # 使用默认路径: Downloads/VidFlow
            import os
            from pathlib import Path
            home_dir = os.path.expanduser("~")
            output_path = os.path.join(home_dir, "Downloads", "VidFlow")
            # 确保目录存在
            Path(output_path).mkdir(parents=True, exist_ok=True)

        # 创建数据库任务记录
        task = DownloadTask(
            task_id=task_id,
            url=request.url,
            title=video_info.get('title'),
            platform=video_info.get('platform'),
            thumbnail=video_info.get('thumbnail'),
            duration=video_info.get('duration'),
            quality=effective_quality,
            format_id=request.format_id,
            output_path=output_path,
            status='pending'
        )

        db.add(task)
        await db.commit()
        await db.refresh(task)

        # 添加到下载队列
        await download_queue.add_task(task_id)

        # 启动队列处理器（如果有空闲槽位）
        import asyncio
        t = asyncio.create_task(_process_queue())
        t.add_done_callback(_handle_task_exception)

        # 获取队列状态
        queue_status = await download_queue.get_status()

        return {
            "status": "success",
            "task_id": task_id,
            "message": "Download task created and added to queue",
            "video_info": video_info,
            "queue_status": queue_status
        }
    except HTTPException:
        # 已经是 HTTPException，直接抛出
        raise
    except Exception as e:
        logger.error(f"Error starting download: {e}")
        # 返回 400 而不是 500，因为这通常是用户可操作的错误
        error_msg = str(e)
        # 如果错误信息中包含用户友好的提示，直接返回
        if "💡" in error_msg or "解决方法" in error_msg:
            raise HTTPException(status_code=400, detail=error_msg)
        # 否则包装一下
        raise HTTPException(status_code=400, detail=f"启动下载失败: {error_msg}")

async def _process_queue():
    """处理下载队列"""
    try:
        # 尝试启动下一个任务
        next_task_id = await download_queue.start_next_task()
        if next_task_id:
            logger.info(f"Processing next task from queue: {next_task_id}")
            # 获取任务信息 - 使用 AsyncSessionLocal 而不是 get_session
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(DownloadTask).where(DownloadTask.task_id == next_task_id)
                )
                task = result.scalar_one_or_none()
                if task:
                    # 创建请求对象
                    request = DownloadRequest(
                        url=task.url,
                        quality=task.quality or 'best',
                        output_path=task.output_path,
                        format_id=task.format_id
                    )
                    # 执行下载 - 不传递db会话，让任务自己创建新会话
                    import asyncio
                    t = asyncio.create_task(_execute_download(next_task_id, request))
                    # 注册运行中的任务以便取消
                    await download_queue.register_running_task(next_task_id, t)
                    t.add_done_callback(_handle_task_exception)
    except Exception as e:
        logger.error(f"Error processing queue: {e}")


async def _execute_download(task_id: str, request: DownloadRequest):
    """在后台执行下载"""
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(DownloadTask).where(DownloadTask.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                logger.error(f"Download task not found: {task_id}")
                return

            task.status = 'downloading'
            task.started_at = datetime.now()
            await db.commit()

            # 进度更新的节流控制
            last_db_update = [0.0]  # 使用列表以便在闭包中修改
            # 取消检查标志
            cancel_check_interval = 1.0  # 每秒检查一次取消状态
            last_cancel_check = [0.0]

            async def progress_callback(progress_data: dict):
                """更新任务进度到数据库并通过 WebSocket 推送"""
                try:
                    import time
                    current_time = time.time()

                    # 检查任务是否被取消（每秒检查一次）
                    if current_time - last_cancel_check[0] >= cancel_check_interval:
                        last_cancel_check[0] = current_time
                        is_cancelled = await download_queue.is_task_cancelled(task_id)
                        if is_cancelled:
                            logger.info(f"Task {task_id} cancellation detected in progress callback")
                            # 抛出取消异常，这会被 yt-dlp 捕获并停止下载
                            raise asyncio.CancelledError(f"Task {task_id} was cancelled by user")

                    if progress_data.get('status') != 'downloading':
                        return

                    # 兼容不同下载器的字段
                    raw_progress = progress_data.get('progress', progress_data.get('percentage', 0))
                    progress = round(float(raw_progress or 0), 1)
                    downloaded = progress_data.get('downloaded', progress_data.get('downloaded_bytes', 0)) or 0
                    total = progress_data.get('total', progress_data.get('total_bytes', 0)) or 0
                    speed = progress_data.get('speed', 0) or 0
                    eta = progress_data.get('eta', 0) or 0

                    # 先通过 WebSocket 推送（实时更新UI）
                    try:
                        from src.core.websocket_manager import get_ws_manager
                        ws_manager = get_ws_manager()
                        await ws_manager.send_download_progress(task_id, {
                            "progress": progress,
                            "downloaded": downloaded,
                            "total": total,
                            "speed": speed,
                            "eta": eta,
                            "status": "downloading"
                        })
                    except Exception as push_err:
                        logger.debug(f"WS push failed for {task_id}: {push_err}")

                    # 降低数据库写入频率：每2秒或进度变化超过5%才写入数据库
                    should_update_db = (
                        current_time - last_db_update[0] >= 2.0 or  # 每2秒更新一次
                        abs(progress - (task.progress or 0)) >= 5.0  # 进度变化超过5%
                    )

                    if should_update_db:
                        # 使用独立的数据库会话避免冲突
                        async with AsyncSessionLocal() as update_db:
                            try:
                                result = await update_db.execute(
                                    select(DownloadTask).where(DownloadTask.task_id == task_id)
                                )
                                update_task = result.scalar_one_or_none()
                                if update_task:
                                    update_task.progress = progress
                                    update_task.downloaded_bytes = downloaded
                                    update_task.total_bytes = total
                                    update_task.speed = speed
                                    update_task.eta = eta
                                    await update_db.commit()
                                    last_db_update[0] = current_time
                                    logger.debug(f"Progress saved to DB for {task_id}: {progress:.1f}%")
                            except Exception as db_err:
                                logger.warning(f"DB update skipped for {task_id}: {db_err}")
                                await update_db.rollback()

                except asyncio.CancelledError:
                    # 重新抛出取消异常
                    raise
                except Exception as e:
                    logger.error(f"Error updating progress for {task_id}: {e}")

            result_data = await downloader.download_video(
                url=request.url,
                quality=request.quality,
                output_path=request.output_path,
                format_id=request.format_id,
                task_id=task_id,
                progress_callback=progress_callback
            )

            _apply_download_result(task, result_data)
            await db.commit()

            # 发送完成状态的 WebSocket 消息，让前端立即更新 UI
            ws_manager = get_ws_manager()
            # 构建完整的文件路径（file_path 不是模型属性，需要动态计算）
            file_path = None
            if task.filename and task.output_path:
                import os
                if os.path.isabs(task.filename):
                    file_path = task.filename
                else:
                    file_path = os.path.join(task.output_path, task.filename)
            await ws_manager.send_download_progress(task_id, {
                'status': 'completed',
                'progress': 100.0,
                'filename': task.filename,
                'file_path': file_path,
                'filesize': task.filesize,
                'quality': task.quality,
            })

            logger.info(f"Download completed: {task_id}")

        except asyncio.CancelledError:
            # 检查是暂停还是取消
            is_paused = await download_queue.is_task_paused(task_id)

            if is_paused:
                logger.info(f"Download paused: {task_id}")
                # 暂停时不更新状态，因为 pause_task API 已经更新了
            else:
                logger.info(f"Download cancelled: {task_id}")
                # 重新获取任务对象以更新状态
                try:
                    # CancelledError 发生时，原 session 可能已关闭或不稳定，重新获取
                    async with AsyncSessionLocal() as cancel_db:
                        result = await cancel_db.execute(
                            select(DownloadTask).where(DownloadTask.task_id == task_id)
                        )
                        task = result.scalar_one_or_none()
                        if task and task.status not in ['paused', 'cancelled']:
                            task.status = 'cancelled'
                            task.error_message = '用户取消下载'
                            await cancel_db.commit()
                except Exception as e:
                    logger.error(f"Error updating task status on cancel: {e}")
            raise  # 重新抛出，让外层处理

        except Exception as e:
            error_msg = str(e)

            # 检查是否是用户取消的下载
            if 'cancelled by user' in error_msg.lower() or 'download cancelled' in error_msg.lower():
                logger.info(f"Download cancelled by user: {task_id}")
                try:
                    async with AsyncSessionLocal() as cancel_db:
                        result = await cancel_db.execute(
                            select(DownloadTask).where(DownloadTask.task_id == task_id)
                        )
                        task = result.scalar_one_or_none()
                        if task:
                            task.status = 'cancelled'
                            task.error_message = '用户取消下载'
                            await cancel_db.commit()
                except Exception as cancel_err:
                    logger.error(f"Error updating task status on cancel: {cancel_err}")
            else:
                logger.error(f"Download failed: {e}")
                # 使用新的数据库会话来更新失败状态，避免会话状态损坏问题
                try:
                    async with AsyncSessionLocal() as fail_db:
                        result = await fail_db.execute(
                            select(DownloadTask).where(DownloadTask.task_id == task_id)
                        )
                        task = result.scalar_one_or_none()
                        if task:
                            task.status = 'failed'
                            task.error_message = error_msg
                            await fail_db.commit()
                except Exception as fail_err:
                    logger.error(f"Error updating task status on failure: {fail_err}")
        finally:
            # 从队列中移除完成/失败的任务
            await download_queue.complete_task(task_id)
            # 处理队列中的下一个任务（asyncio 已在文件顶部导入）
            t = asyncio.create_task(_process_queue())
            t.add_done_callback(_handle_task_exception)

@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """
    取消下载任务
    """
    try:
        success = await download_queue.cancel_task(task_id)

        # 即使队列中没有（可能已完成），也要更新数据库状态
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DownloadTask).where(DownloadTask.task_id == task_id)
            )
            task = result.scalar_one_or_none()

            if task and task.status in ['pending', 'downloading', 'paused']:
                task.status = 'cancelled'
                task.error_message = '用户取消下载'
                await db.commit()

        if success:
            return {"status": "success", "message": "Task cancelled"}
        else:
            # 如果队列中没有，可能已经完成或失败
            return {"status": "warning", "message": "Task not active or already cancelled"}

    except Exception as e:
        logger.error(f"Error cancelling task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    """
    暂停下载任务
    """
    try:
        # 获取任务信息
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DownloadTask).where(DownloadTask.task_id == task_id)
            )
            task = result.scalar_one_or_none()

            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            if task.status not in ['pending', 'downloading']:
                raise HTTPException(status_code=400, detail=f"Cannot pause task with status: {task.status}")

            # 保存任务信息用于恢复
            task_info = {
                'url': task.url,
                'quality': task.quality,
                'format_id': task.format_id,
                'output_path': task.output_path,
                'progress': task.progress,
                'downloaded_bytes': task.downloaded_bytes,
                'total_bytes': task.total_bytes,
            }

            # 暂停任务
            success = await download_queue.pause_task(task_id, task_info)

            if success:
                # 更新数据库状态
                task.status = 'paused'
                await db.commit()

                # 通过 WebSocket 通知前端
                try:
                    from src.core.websocket_manager import get_ws_manager
                    ws_manager = get_ws_manager()
                    await ws_manager.send_download_progress(task_id, {
                        "progress": task.progress or 0,
                        "status": "paused",
                        "message": "下载已暂停"
                    })
                except Exception as ws_err:
                    logger.debug(f"WS notification failed: {ws_err}")

                return {"status": "success", "message": "Task paused"}
            else:
                return {"status": "warning", "message": "Task could not be paused"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    """
    恢复暂停的下载任务
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DownloadTask).where(DownloadTask.task_id == task_id)
            )
            task = result.scalar_one_or_none()

            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            if task.status != 'paused':
                raise HTTPException(status_code=400, detail=f"Cannot resume task with status: {task.status}")

            # 从队列中恢复任务
            await download_queue.resume_task(task_id)

            # 更新状态为 pending，等待重新下载
            task.status = 'pending'
            await db.commit()

            # 重新添加到队列
            await download_queue.add_task(task_id)

            # 启动队列处理器
            import asyncio
            t = asyncio.create_task(_process_queue())
            t.add_done_callback(_handle_task_exception)

            # 通过 WebSocket 通知前端
            try:
                from src.core.websocket_manager import get_ws_manager
                ws_manager = get_ws_manager()
                await ws_manager.send_download_progress(task_id, {
                    "progress": task.progress or 0,
                    "status": "pending",
                    "message": "下载已恢复，等待中..."
                })
            except Exception as ws_err:
                logger.debug(f"WS notification failed: {ws_err}")

            return {"status": "success", "message": "Task resumed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/tasks")
async def get_tasks(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_session)
):
    """
    获取所有下载任务
    """
    try:
        query = select(DownloadTask).order_by(desc(DownloadTask.created_at))

        # 筛选状态
        if status:
            query = query.where(DownloadTask.status == status)

        # 分页
        query = query.limit(limit).offset(offset)

        result = await db.execute(query)
        tasks = result.scalars().all()

        return {
            "status": "success",
            "tasks": [task.to_dict() for task in tasks],
            "total": len(tasks)
        }
    except Exception as e:
        logger.error(f"Error getting tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    db: AsyncSession = Depends(get_session)
):
    """
    获取任务状态
    """
    try:
        result = await db.execute(
            select(DownloadTask).where(DownloadTask.task_id == task_id)
        )
        task = result.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return {
            "status": "success",
            "task": task.to_dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    delete_file: bool = False,
    db: AsyncSession = Depends(get_session)
):
    """
    删除任务

    Args:
        task_id: 任务ID
        delete_file: 是否同时删除本地文件，默认为 False
    """
    try:
        result = await db.execute(
            select(DownloadTask).where(DownloadTask.task_id == task_id)
        )
        task = result.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        file_deleted = False
        file_path = None

        # 如果需要删除本地文件
        if delete_file and task.filename:
            import os
            from pathlib import Path

            # 构建完整的文件路径
            if os.path.isabs(task.filename):
                file_path = task.filename
            elif task.output_path:
                file_path = os.path.join(task.output_path, task.filename)

            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    file_deleted = True
                    logger.info(f"Deleted file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete file {file_path}: {e}")

        await db.delete(task)
        await db.commit()

        return {
            "status": "success",
            "message": "Task deleted",
            "file_deleted": file_deleted,
            "file_path": file_path if file_deleted else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/queue/status")
async def get_queue_status():
    """
    获取下载队列状态
    """
    try:
        status = await download_queue.get_status()
        return {
            "status": "success",
            "queue": status
        }
    except Exception as e:
        logger.error(f"Error getting queue status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/queue/config")
async def update_queue_config(max_concurrent: int):
    """
    更新下载队列配置

    Args:
        max_concurrent: 最大并发下载数 (1-10)
    """
    try:
        if max_concurrent < 1 or max_concurrent > 10:
            raise HTTPException(status_code=400, detail="max_concurrent must be between 1 and 10")

        # 更新队列配置
        await download_queue.update_max_concurrent(max_concurrent)

        # 同时更新配置文件
        config = get_config_manager()
        config.set('download.max_concurrent', max_concurrent)

        logger.info(f"Queue max_concurrent updated to {max_concurrent}")

        return {
            "status": "success",
            "message": "Queue configuration updated",
            "max_concurrent": max_concurrent
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating queue config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
