"""FastAPI 依赖注入：路由层统一从这里拿配置/注册中心等依赖。"""

from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
