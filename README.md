# SUMO GUI 广州城市交通仿真

该项目基于广州珠江新城/天河 CBD 路网，在 SUMO 中展示全天交通需求、实时事件注入、道路施工、恶劣天气、演唱会进散场及车辆重规划。

## 项目目录

- `01_Inputs`：原始输入资料。
- `02_Working`：代码、配置、地图和测试。
- `03_Outputs`：事件日志、交通指标和运行摘要。
- `04_Logs`：测试与复盘记录。

## 全天事件演示

```powershell
cd D:\Workspace\project000_LLM_Sim\02_Working
python scripts/demo_gui_events.py --gui
```

默认仿真覆盖 06:30–22:30，共 57,600 个一秒步长。GUI 默认延迟为 `20 ms/步`，实际运行约 20 分钟；可通过 `--delay` 调整肉眼观察速度：

```powershell
python scripts/demo_gui_events.py --gui --delay 50
```

未指定种子时，每次运行都会重新选择事件位置；需要复现实验时使用：

```powershell
python scripts/demo_gui_events.py --gui --event-seed 12345
```

### 事实时间轴

事件时间以 06:30 为相对时间零点：

- 07:00–09:00：工作日早高峰。
- 09:00–12:00：日间道路施工，影响沿上游和替代道路扩散。
- 14:00–16:00：台风天气。
- 17:00–19:00：工作日晚高峰。
- 18:00–22:30：演唱会交通；19:30 开演，约 21:30 转为散场车流。

`EventManager` 会校验事件时刻。早高峰、晚高峰、演唱会或施工被配置到不合理时段时，默认自动调整并在后台输出 `[合理性校验]`。

时间与交通组织依据：

- 广州市地方标准 DB4401/T 57：工作日早高峰通常为 07:00–09:00，晚高峰通常为 17:00–19:00。
- 广州市文化广电旅游局公开演出清单：广州大量晚间演出在 19:30–20:00 开始。
- 广州市交通运输局施工疏解案例：围蔽施工需要组织上游分流和替代道路绕行，因此施工影响不局限于施工点本身。

### GUI 和后台行为

- 程序不自动缩放、移动镜头或跟随车辆。
- 事件仅使用 2 像素空心圆表示，不遮挡车辆。
- 圆内没有受影响车辆时自动隐藏，车辆出现后在实际车流簇重新显示。
- 路口 Agent 菱形标记始终保留：青色表示正常，黄色表示附近事件监测，红色表示正在发布绕行。
- 后台报告事件启动、阶段变化、真实减速/拥堵/排队/绕行，以及附近 Agent 的观测、阈值判断和执行动作。

### 施工扩散

施工采用交通流中的“容量骤降”建模：优先选择双车道路，将一条施工车道速度降至接近零，同时保留拓扑连接，避免破坏已有车辆路线。施工需求必须经过该瓶颈，因此车辆会自然减速、停车和向上游排队。影响沿路网拓扑扩展最多 4 跳、24 条连接道路，路口 Agent 根据实测队列决定是否发布绕行。

相关参数位于 `02_Working/config/events/demo_gui_events.yaml`：

- `propagation_hops`
- `max_impact_edges`
- `propagation_speed_factors`
- `spawn_interval`
- `inbound_max_count`
- `outbound_max_count`

## 输出

结果写入 `03_Outputs/events`：

- `event_log.csv`：事件启动、阶段变化、实际交通现象和恢复记录。
- `traffic_metrics.csv`：车辆数、速度、等待、停车和拥堵道路数。
- `gui_demo_summary.json`：随机种子、事件道路、最终状态及分阶段车辆数量。

## 测试

```powershell
cd D:\Workspace\project000_LLM_Sim\02_Working
python -m unittest discover -s tests -v
```
