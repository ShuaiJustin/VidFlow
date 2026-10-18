"""
下载 API 测试
"""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import status
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.api import downloads as downloads_api
from src.models.download import DownloadTask


def test_apply_download_result_uses_actual_quality():
    task = DownloadTask(
        task_id='quality-result',
        url='https://www.xinpianchang.com/a13685467',
        quality='1080p',
        status='downloading',
    )

    downloads_api._apply_download_result(task, {
        'filename': 'OPPO - FIND X_1080p.mp4',
        'filesize': 6375673,
        'actual_quality': '720p',
    })

    assert task.status == 'completed'
    assert task.quality == '720p'
    assert task.filename == 'OPPO - FIND X_1080p.mp4'


def test_resolve_requested_quality_uses_highest_available_xinpianchang_format():
    video_info = {
        'platform': 'xinpianchang',
        'formats': [
            {'height': 720, 'quality': '720p'},
            {'height': 540, 'quality': '540p'},
        ],
    }

    assert downloads_api._resolve_requested_quality('1080p', video_info) == '720p'
    assert downloads_api._resolve_requested_quality('540p', video_info) == '540p'
    assert downloads_api._resolve_requested_quality('best', video_info) == 'best'


@pytest.mark.api
class TestDownloadsAPI:
    """下载 API 测试类"""

    @pytest.mark.asyncio
    async def test_get_video_info_success(self, mock_video_info):
        """测试获取视频信息 - 成功"""
        with patch(
            'src.api.downloads.downloader.get_video_info',
            new=AsyncMock(return_value=mock_video_info)
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/downloads/info",
                    json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
                )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert data["data"] == mock_video_info

    @pytest.mark.asyncio
    async def test_get_video_info_invalid_url(self):
        """测试获取视频信息 - 无效 URL"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/downloads/info",
                json={"url": "invalid-url"}
            )

        assert response.status_code == 422  # HTTP_422_UNPROCESSABLE_CONTENT

    @pytest.mark.asyncio
    @pytest.mark.slow
    @pytest.mark.skip(reason="需要完整的数据库环境和yt-dlp工具，存在session管理问题")
    async def test_start_download(self, sample_download_task):
        """测试开始下载（需要实际数据库和 yt-dlp）"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/downloads/start",
                json=sample_download_task
            )

        # 验证响应状态码
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_500_INTERNAL_SERVER_ERROR  # 可能因为 yt-dlp 实际执行失败
        ]

        # 如果成功，验证返回格式
        if response.status_code == status.HTTP_200_OK:
            data = response.json()
            assert "status" in data
            assert "task_id" in data

    @pytest.mark.asyncio
    async def test_get_tasks_list(self):
        """测试获取任务列表"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/downloads/tasks")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # API 返回格式: {"status": "success", "tasks": [...], "total": N}
        assert "status" in data
        assert data["status"] == "success"
        assert "tasks" in data
        assert isinstance(data["tasks"], list)
        assert "total" in data
        assert isinstance(data["total"], int)

    @pytest.mark.asyncio
    async def test_health_check(self):
        """测试健康检查"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
