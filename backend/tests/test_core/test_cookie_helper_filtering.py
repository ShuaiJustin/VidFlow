"""
测试 Cookie Helper 中的无效 Cookie 过滤功能
"""
import pytest
import sys
from pathlib import Path

# 添加 src 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from core.cookie_helper import CookieBrowserManager


class TestCookieFiltering:
    """测试 Cookie 过滤功能"""

    def test_convert_cookies_filters_empty_name(self):
        """测试 convert_cookies_to_netscape 过滤空 name 的 Cookie"""
        helper = CookieBrowserManager()

        cookies = [
            {'name': 'valid_cookie', 'value': 'value1', 'domain': '.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},
            {'name': '', 'value': 'value2', 'domain': '.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},  # 空 name
            {'name': 'another_valid', 'value': 'value3', 'domain': '.douyin.com', 'path': '/', 'secure': True, 'expiry': 1798438806},
        ]

        result = helper.convert_cookies_to_netscape(cookies, '.douyin.com')
        lines = result.strip().split('\n')

        # 应该有 2 个有效 Cookie + 2 行头部注释 + 1 空行 = 5 行
        data_lines = [l for l in lines if l and not l.startswith('#')]
        assert len(data_lines) == 2

        # 验证有效 Cookie 存在
        assert any('valid_cookie' in line for line in data_lines)
        assert any('another_valid' in line for line in data_lines)

        # 验证空 name 的 Cookie 被过滤
        assert not any('\t\t' in line for line in data_lines)  # 空 name 会导致连续 tab

    def test_convert_cookies_filters_empty_domain(self):
        """测试 convert_cookies_to_netscape 过滤空 domain 的 Cookie"""
        helper = CookieBrowserManager()

        cookies = [
            {'name': 'valid_cookie', 'value': 'value1', 'domain': '.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},
            {'name': 'no_domain_cookie', 'value': 'value2', 'domain': '', 'path': '/', 'secure': False, 'expiry': 1798438806},  # 空 domain
            {'name': 'another_valid', 'value': 'value3', 'domain': 'www.douyin.com', 'path': '/', 'secure': True, 'expiry': 1798438806},
        ]

        result = helper.convert_cookies_to_netscape(cookies, '')  # 传入空 domain 作为默认值
        lines = result.strip().split('\n')

        data_lines = [l for l in lines if l and not l.startswith('#')]
        assert len(data_lines) == 2

        # 验证有效 Cookie 存在
        assert any('valid_cookie' in line for line in data_lines)
        assert any('another_valid' in line for line in data_lines)

        # 验证空 domain 的 Cookie 被过滤
        assert not any('no_domain_cookie' in line for line in data_lines)

    def test_convert_cookies_filters_missing_name(self):
        """测试 convert_cookies_to_netscape 过滤缺少 name 字段的 Cookie"""
        helper = CookieBrowserManager()

        cookies = [
            {'name': 'valid_cookie', 'value': 'value1', 'domain': '.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},
            {'value': 'value2', 'domain': '.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},  # 缺少 name
        ]

        result = helper.convert_cookies_to_netscape(cookies, '.douyin.com')
        lines = result.strip().split('\n')

        data_lines = [l for l in lines if l and not l.startswith('#')]
        assert len(data_lines) == 1
        assert 'valid_cookie' in data_lines[0]

    def test_convert_cookies_handles_all_invalid(self):
        """测试所有 Cookie 都无效时的处理"""
        helper = CookieBrowserManager()

        cookies = [
            {'name': '', 'value': 'value1', 'domain': '.douyin.com', 'path': '/', 'secure': False},
            {'name': 'cookie2', 'value': 'value2', 'domain': '', 'path': '/', 'secure': False},
        ]

        result = helper.convert_cookies_to_netscape(cookies, '')
        lines = result.strip().split('\n')

        # 只有头部注释，没有数据行
        data_lines = [l for l in lines if l and not l.startswith('#')]
        assert len(data_lines) == 0

    def test_convert_cookies_preserves_valid_cookies(self):
        """测试有效 Cookie 被正确保留"""
        helper = CookieBrowserManager()

        cookies = [
            {'name': 'session_id', 'value': 'abc123', 'domain': '.douyin.com', 'path': '/', 'secure': True, 'expiry': 1798438806},
            {'name': 'user_token', 'value': 'xyz789', 'domain': 'www.douyin.com', 'path': '/api', 'secure': False, 'expiry': 1798438806},
        ]

        result = helper.convert_cookies_to_netscape(cookies, '.douyin.com')
        lines = result.strip().split('\n')

        data_lines = [l for l in lines if l and not l.startswith('#')]
        assert len(data_lines) == 2

        # 验证格式正确
        for line in data_lines:
            fields = line.split('\t')
            assert len(fields) == 7  # Netscape 格式有 7 个字段
            assert fields[1] in ('TRUE', 'FALSE')  # flag
            assert fields[3] in ('TRUE', 'FALSE')  # secure
            assert fields[4].isdigit()  # expiry

    def test_convert_cookies_domain_flag_logic(self):
        """测试 domain flag 逻辑正确"""
        helper = CookieBrowserManager()

        cookies = [
            {'name': 'cookie1', 'value': 'v1', 'domain': '.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},  # 以 . 开头
            {'name': 'cookie2', 'value': 'v2', 'domain': 'www.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},  # 不以 . 开头
        ]

        result = helper.convert_cookies_to_netscape(cookies, '.douyin.com')
        lines = result.strip().split('\n')

        data_lines = [l for l in lines if l and not l.startswith('#')]

        # 找到对应的行
        for line in data_lines:
            fields = line.split('\t')
            if fields[0] == '.douyin.com':
                assert fields[1] == 'TRUE'  # 以 . 开头，flag 应该是 TRUE
            elif fields[0] == 'www.douyin.com':
                assert fields[1] == 'FALSE'  # 不以 . 开头，flag 应该是 FALSE


class TestManagedBrowserSessionStatus:
    def test_xinpianchang_cdp_capture_succeeds_without_session_cookies(self):
        class CdpSession:
            def snapshot(self):
                return {
                    'current_url': 'https://www.xinpianchang.com/a13685467',
                    'cookies': [],
                    'capture': {
                        'page_url': 'https://www.xinpianchang.com/a13685467',
                        'title': 'Persisted verified profile',
                        'description': '',
                        'thumbnail': '',
                        'media_url': 'https://us-xpc5-l2.xpccdn.com/video.mp4?signature=valid',
                        'duration': 18.5,
                    },
                }

        helper = CookieBrowserManager()
        helper.cdp_session = CdpSession()
        helper.is_running = True
        helper.current_platform = 'xinpianchang'
        helper.current_browser = 'chrome'

        result = helper._extract_cookies_sync()

        assert result['status'] == 'success'
        assert result['count'] == 0
        assert helper.get_page_capture('https://www.xinpianchang.com/a13685467')

    def test_extract_cookies_uses_xinpianchang_cdp_session(self):
        class CdpSession:
            def snapshot(self):
                return {
                    'current_url': 'https://www.xinpianchang.com/a13685467',
                    'cookies': [{
                        'name': 'FECW',
                        'value': 'verified',
                        'domain': '.xinpianchang.com',
                        'path': '/',
                        'secure': True,
                        'expiry': 1818336339,
                    }],
                    'capture': {
                        'page_url': 'https://www.xinpianchang.com/a13685467',
                        'title': 'CDP verified video',
                        'description': '',
                        'thumbnail': '',
                        'media_url': 'https://us-xpc5-l2.xpccdn.com/video.mp4?signature=valid',
                        'duration': 18.5,
                    },
                }

        helper = CookieBrowserManager()
        helper.cdp_session = CdpSession()
        helper.is_running = True
        helper.current_platform = 'xinpianchang'
        helper.current_browser = 'chrome'

        result = helper._extract_cookies_sync()
        capture = helper.get_page_capture('https://www.xinpianchang.com/a13685467')

        assert result['status'] == 'success'
        assert 'FECW' in result['content']
        assert capture['title'] == 'CDP verified video'

    def test_status_includes_active_browser_for_session_recovery(self):
        """The UI needs the original browser when restoring a managed session."""
        helper = CookieBrowserManager()
        helper.is_running = True
        helper.current_platform = 'xinpianchang'
        helper.current_browser = 'chrome'

        status = helper.get_status()

        assert status['is_running'] is True
        assert status['platform'] == 'xinpianchang'
        assert status['browser'] == 'chrome'

    def test_target_url_is_limited_to_the_platform_domain(self):
        helper = CookieBrowserManager()
        target = 'https://www.xinpianchang.com/a13685467?from=search'

        assert helper._resolve_target_url('xinpianchang', target) == target
        assert helper._resolve_target_url(
            'xinpianchang',
            'https://example.com/not-allowed',
        ) == 'https://www.xinpianchang.com/'

    def test_extract_cookies_captures_xinpianchang_media_before_browser_closes(self):
        class SwitchTo:
            def window(self, _handle):
                return None

        class Driver:
            window_handles = ['main']
            switch_to = SwitchTo()
            current_url = 'https://www.xinpianchang.com/a13727498?from=ArticleList'

            def get_cookies(self):
                return [{
                    'name': '__ws_ch_ck',
                    'value': 'challenge',
                    'domain': '.xinpianchang.com',
                    'path': '/',
                    'secure': True,
                    'expiry': 1818336339,
                }]

            def execute_script(self, _script):
                return {
                    'page_url': self.current_url,
                    'title': 'Browser verified video',
                    'description': 'Captured in the verified browser session',
                    'thumbnail': 'https://cdn.example/poster.jpg',
                    'media_url': 'https://us-xpc5-l2.xpccdn.com/video.mp4?signature=valid',
                    'duration': 42.5,
                }

        helper = CookieBrowserManager()
        helper.driver = Driver()
        helper.is_running = True
        helper.current_platform = 'xinpianchang'

        result = helper._extract_cookies_sync()
        capture = helper.get_page_capture('https://www.xinpianchang.com/a13727498')

        assert result['status'] == 'success'
        assert capture['title'] == 'Browser verified video'
        assert capture['media_url'].startswith('https://us-xpc5-l2.xpccdn.com/video.mp4')


class TestRealWorldScenario:
    """测试真实场景"""

    def test_douyin_cookie_with_empty_name_filtered(self):
        """测试抖音 Cookie 中空 name 的行被过滤"""
        helper = CookieBrowserManager()

        # 模拟用户报告的问题场景
        cookies = [
            {'name': 'odin_tt', 'value': 'c4114197ff7390ac84a554716117c38db2d90792717e0c6ae5cb6001d2da6635', 'domain': '.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438781},
            {'name': '', 'value': 'douyin.com', 'domain': 'www.douyin.com', 'path': '/', 'secure': False, 'expiry': 1798438806},  # 问题行：空 name
            {'name': 'sessionid', 'value': 'ea5dfb3bfffef1668bf62ea42ec0b7c2', 'domain': '.douyin.com', 'path': '/', 'secure': True, 'expiry': 1772086773},
        ]

        result = helper.convert_cookies_to_netscape(cookies, '.douyin.com')
        lines = result.strip().split('\n')

        data_lines = [l for l in lines if l and not l.startswith('#')]

        # 应该只有 2 个有效 Cookie
        assert len(data_lines) == 2

        # 验证有效 Cookie 存在
        assert any('odin_tt' in line for line in data_lines)
        assert any('sessionid' in line for line in data_lines)

        # 验证问题行被过滤（不应该有空 name 的行）
        for line in data_lines:
            fields = line.split('\t')
            assert len(fields) == 7
            assert fields[5] != ''  # name 字段不应该为空
