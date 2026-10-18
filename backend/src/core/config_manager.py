"""
Configuration manager for persisted user settings.
"""
import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_VERSION = "1.0.0"

if getattr(sys, 'frozen', False):
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        BASE_DIR = Path(appdata) / 'VidFlow'
    elif sys.platform == 'darwin':
        BASE_DIR = Path.home() / 'Library' / 'Application Support' / 'VidFlow'
    else:
        BASE_DIR = Path.home() / '.local' / 'share' / 'VidFlow'
    BASE_DIR.mkdir(parents=True, exist_ok=True)
else:
    BASE_DIR = Path(__file__).parent.parent.parent

DATA_DIR = BASE_DIR / 'data'
CONFIG_FILE = DATA_DIR / 'config.json'


def get_default_download_path() -> str:
    try:
        downloads = Path.home() / 'Downloads'
        if downloads.exists():
            vidflow_dir = downloads / 'VidFlow'
            vidflow_dir.mkdir(parents=True, exist_ok=True)
            return str(vidflow_dir)

        fallback = DATA_DIR / 'downloads'
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)
    except Exception as exc:
        logger.error('Failed to get default download path: %s', exc)
        fallback = DATA_DIR / 'downloads'
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)


def get_default_config() -> Dict[str, Any]:
    return {
        '_config_version': CONFIG_VERSION,
        'app': {
            'version': '3.1.0',
            'first_run': True,
            'language': 'zh-CN',
            'theme': 'light',
        },
        'download': {
            'default_path': get_default_download_path(),
            'default_quality': '1080p',
            'default_format': 'mp4',
            'max_concurrent': 3,
            'auto_subtitle': False,
            'auto_translate': False,
        },
        'subtitle': {
            'default_model': 'base',
            'default_source_lang': 'auto',
            'default_target_langs': ['zh'],
            'max_concurrent': 1,
        },
        'advanced': {
            'proxy': {
                'enabled': False,
                'type': 'http',
                'host': '',
                'port': 0,
                'username': '',
                'password': '',
            },
            'notifications': True,
            'auto_update': True,
            'update_server_url': '',
            'save_history': True,
            'log_level': 'INFO',
            'log_format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        },
    }


def migrate_config(old_config: Dict[str, Any], old_version: str) -> Dict[str, Any]:
    migrated = dict(old_config) if isinstance(old_config, dict) else {}

    try:
        download_cfg = migrated.get('download')
        if isinstance(download_cfg, dict):
            current_path = download_cfg.get('default_path')
            if current_path is None or (isinstance(current_path, str) and not current_path.strip()):
                download_cfg['default_path'] = get_default_download_path()
    except Exception as exc:
        logger.warning('Failed to migrate config from version %s: %s', old_version, exc)

    return migrated


class ConfigManager:
    def __init__(self, config_file: Optional[Path] = None):
        self.config_file = config_file or CONFIG_FILE
        self.config: Dict[str, Any] = {}
        self.load_config()

    def load_config(self) -> Dict[str, Any]:
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as handle:
                    loaded_config = json.load(handle)

                config_version = '0.0.0'
                if isinstance(loaded_config, dict):
                    config_version = str(loaded_config.get('_config_version', '0.0.0'))
                else:
                    loaded_config = {}

                if config_version != CONFIG_VERSION:
                    loaded_config = migrate_config(loaded_config, config_version)
                    if isinstance(loaded_config, dict):
                        loaded_config['_config_version'] = CONFIG_VERSION

                self.config = self._merge_config(get_default_config(), loaded_config)
                logger.info('Configuration loaded from %s', self.config_file)

                if config_version != CONFIG_VERSION:
                    self.save_config()
            else:
                self.config = get_default_config()
                self.save_config()
                logger.info('Created default configuration')
        except Exception as exc:
            logger.error('Failed to load config: %s', exc)
            self.config = get_default_config()

        return self.config

    def save_config(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)

            if isinstance(self.config, dict) and self.config.get('_config_version') != CONFIG_VERSION:
                self.config['_config_version'] = CONFIG_VERSION

            with open(self.config_file, 'w', encoding='utf-8') as handle:
                json.dump(self.config, handle, indent=2, ensure_ascii=False)

            logger.info('Configuration saved to %s', self.config_file)
        except Exception as exc:
            logger.error('Failed to save config: %s', exc)

    def get(self, key_path: str, default: Any = None) -> Any:
        value: Any = self.config
        for key in key_path.split('.'):
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, key_path: str, value: Any):
        keys = key_path.split('.')
        config = self.config

        for key in keys[:-1]:
            if key not in config or not isinstance(config[key], dict):
                config[key] = {}
            config = config[key]

        config[keys[-1]] = value
        self.save_config()

    def update(self, updates: Dict[str, Any]):
        self.config = self._merge_config(self.config, updates)
        self.save_config()

    def reset(self):
        self.config = get_default_config()
        self.save_config()
        logger.info('Configuration reset to default')

    def _merge_config(self, base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        result = base.copy()
        for key, value in updates.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def get_all(self) -> Dict[str, Any]:
        return copy.deepcopy(self.config)


_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


DEFAULT_CONFIG = get_default_config()