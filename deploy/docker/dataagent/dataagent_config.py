"""DataAgent 外置基础配置（仓库自带，参考 Superset docker/pythonpath_dev 模式）。

本文件由 compose 以目录形式挂载进 dataagent-backend 容器
（`./docker/dataagent` → `/app/docker/dataagent`），并经
`DATAAGENT_CONFIG=/app/docker/dataagent/dataagent_config.py` 指定为外置配置
入口。加载器会把本目录加入 `sys.path`，因此文件末尾可以像 Superset 的
`superset_config.py` 一样加载同目录下的用户扩展模块。

可配置内容：
- 认证（始终启用）：`SECRET_KEY` / `DEFAULT_USER_PASSWORD` /
  `OAUTH_PROVIDERS` / `OAUTH_USER_INFO`（UserInfo 钩子见
  custom_sso_user_info.py.example）。用户、角色和助手授权统一存库管理，
  不再从配置文件读取 `LOCAL_ADMINS` 或 `ADMIN_USERS`。
- 其他运行时配置：`DATAAGENT_SETTINGS = {"<config.py Settings 字段>": 值}`，
  启动时统一应用（如超时/并发档位），字段名见
  `dataagent-backend/config.py`。

使用方式（不要直接改本文件，升级时会被覆盖）：
1. 拷贝 `dataagent_config_docker.py.example` 为 `dataagent_config_docker.py`
   （该文件已被 .gitignore 忽略，不会入库），按注释填写 `SECRET_KEY` 等项。
2. 首次部署执行 `python -m core.accounts create-user --username admin --password ... --admin`
   创建初始管理员账号。
3. 重启 dataagent-backend。

语义（fail-closed）：
- 始终启用认证；设置 `AUTH_ENABLED = False`、缺合法 `SECRET_KEY`、文件不可读、
  语法错误或 `DATAAGENT_SETTINGS` 含未知字段均会导致启动失败，绝不静默降级。
"""
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 默认值（可被 dataagent_config_docker.py 覆盖）
# ---------------------------------------------------------------------------
SECRET_KEY = ""
DEFAULT_USER_PASSWORD = ""

SESSION_TTL_SECONDS = 28800

COOKIE_NAME = "da_session"
COOKIE_SECURE = False
COOKIE_SAMESITE = "lax"

OAUTH_PROVIDERS = []
OAUTH_USER_INFO = None

# 非认证的运行时 Settings 覆盖（config.py 字段），默认不覆盖任何值。
DATAAGENT_SETTINGS = {}

# ---------------------------------------------------------------------------
# 加载同目录下的用户扩展（Superset superset_config_docker.py 同款机制）。
# ---------------------------------------------------------------------------
import importlib.util as _importlib_util

if _importlib_util.find_spec("dataagent_config_docker") is not None:
    import dataagent_config_docker
    from dataagent_config_docker import *  # noqa: F401,F403

    logger.info(
        "Loaded your DataAgent configuration override at [%s]",
        dataagent_config_docker.__file__,
    )
else:
    logger.info("Using default DataAgent config (no dataagent_config_docker.py found)")
