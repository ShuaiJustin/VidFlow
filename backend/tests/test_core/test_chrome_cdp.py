from pathlib import Path

from src.core.chrome_cdp import build_chrome_command, select_page_target


def test_build_chrome_command_uses_persistent_profile_without_automation_flags():
    command = build_chrome_command(
        Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
        Path(r'C:\Users\test\AppData\Roaming\VidFlow\data\browser_profiles\xinpianchang'),
        'https://www.xinpianchang.com/a13685467',
        45678,
    )

    joined = ' '.join(command)
    assert '--remote-debugging-port=45678' in command
    assert '--remote-debugging-port=0' not in command
    assert '--user-data-dir=C:\\Users\\test\\AppData\\Roaming\\VidFlow\\data\\browser_profiles\\xinpianchang' in command
    assert '--enable-automation' not in joined
    assert 'AutomationControlled' not in joined
    assert 'webdriver' not in joined.lower()


def test_select_page_target_prefers_the_requested_xinpianchang_work():
    targets = [
        {
            'type': 'page',
            'url': 'https://www.xinpianchang.com/',
            'webSocketDebuggerUrl': 'ws://127.0.0.1/home',
        },
        {
            'type': 'page',
            'url': 'https://www.xinpianchang.com/a13685467?from=search_post',
            'webSocketDebuggerUrl': 'ws://127.0.0.1/work',
        },
    ]

    target = select_page_target(
        targets,
        'https://www.xinpianchang.com/a13685467?searchKw=oppo',
    )

    assert target['webSocketDebuggerUrl'] == 'ws://127.0.0.1/work'
