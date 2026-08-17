# Results 前端对齐状态

## 结论

Results 三工作区前端已完成代码与契约收口。页面只消费正式 Run/Results API，不读取旧结果目录，不调用旧 summary/compare/run_detail 接口，不在前端重算可比性、R0/R1/R2 差值、资源比例或机场承接占比。

## 三工作区

### 1. 损毁影响与优化效果

- 角色固定为真实 Run：R0 / R1 / R2；
- 候选组合来自后端 `damage-candidates`，前端不自行匹配；
- 损毁影响 = R1-R0；
- 组选调整 = R2-R1；
- 四张摘要卡、差值概览、时序、全机场、资源和方案结构均直接使用 `comparison.v1`。

### 2. 多场景比较

- 选择 2–6 个后端判定可比的 succeeded Run；
- 仅允许损毁场景不同；
- 展示后端给出的各 Run 摘要、完整时序和确定性极值；
- 极值只描述差异，不生成“最佳场景”或自动排序结论。

### 3. 方案配置比较

- 选择 2–5 个 succeeded Run；
- 显式指定 baseline Run；
- 同 Situation / Damage / solver time limit / algorithm seed；
- 允许偏好、alpha、组选开关/规模、核心机场、机型权重等业务配置变化；
- 所有差值均由后端相对 baseline 生成。

## 页面结构

已实现：

- 三工作区顶部页签；
- 覆盖式“修改比较条件”；
- 四张指标卡：出动高峰、峰值出动量、最大机场累计承接占比、资源最低余量；
- 主时序图：全部 / 机场 / 任务 / 机型；
- 差异概览；
- 底部：全机场承接 / 资源变化 / 方案结构；
- 机场值模式：出动架次 / 累计承接占比。

全机场输出完整保留，不做 Top-N，不生成“其他机场”。

## 本轮修正

R0/R1/R2 的机场 `departure_share` 已直接加入后端 comparison 输出。前端不再用“机场出动量 / 总调度量”自行计算累计承接占比。

## 明确不做

- 不输出 completion ratio / shortfall / unmet 等成功正常 Run 核心展示；
- 不自动判断最佳方案；
- 不根据 HHI、资源比例或峰值发明阈值；
- 不接旧 Results API；
- “导出报告”当前保持禁用，等待正式导出契约。

## 验证

当前统一回归：251 tests passed，0 failed。

同时完成无 Flask 前端静态构建门：

- 4 个 Jinja 页面完整渲染，无未解析表达式；
- 5 个 JS module 通过 Node syntax check；
- Python compileall 通过；
- 在线 CDN / 外部前端资产引用扫描为 0；
- legacy Scene/Runtime/Results/Dispatch API 引用扫描为 0。

真实 Flask/TestClient、浏览器截图/交互 smoke 按当前人工决定后移到 Codex/部署依赖阶段。
