"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import ownership, resource

# 凭据子路径（/api/ownership_credential/issue 等）必须先于通用资源路由注册，
# 否则会被 /api/{module}/{entry_id} 的整数路径抢先匹配。
ROUTERS = [ownership, resource]
