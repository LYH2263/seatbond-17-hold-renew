# SeatBond

影院连座锁座：按场次厅图查找连续空座，过道列断开，冲突检测既有持座。

持座有时效：创建即写入到期时刻（`HOLD_TTL_MINUTES`，默认 30 分钟），超时扫描（`POST /api/holds/sweep`，读接口也会惰性触发）把过期持座改为已释放并腾空座位图。持有中且未过期的持座可续期（`POST /api/holds/{id}/renew`），每次顺延 `RENEW_EXTENSION_MINUTES` 并消耗一次续期额度（上限 `MAX_RENEWALS`，默认 2，列表与接口返回 `renewals_remaining`）；已释放、已取消不可续期。也可主动取消（`POST /api/holds/{id}/cancel`）。

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4100 |
| API | http://localhost:9100 |
| API 文档 | http://localhost:9100/docs |
| Postgres | localhost:5442 |

健康检查：`GET http://localhost:9100/api/health`

> 若用旧数据卷启动过，本次加了 `expires_at`/`renew_count` 列，请 `docker compose down -v` 后重建。

## 页面

- `/halls` — 影厅
- `/showtimes` — 场次
- `/seatmap` — 座位图（大网格热力）
- `/hold` — 锁座
- `/orders` — 订单（状态筛选、到期时间、剩余续期、续期/取消）
- `/conflicts` — 冲突

## 使用说明

1. 在影厅与场次页确认厅图与排期。
2. 打开座位图查看占用热力，在锁座页输入连座人数并提交。
3. 订单页查看持座结果、按状态筛选并对持有中的订单续期或取消；冲突页查看重叠请求。

## 开发与测试

```bash
docker compose exec api pytest -q
```
