# 基础数据多格式文件与 SM4

## 实施前检查与范围

当前导入链路：`base-data.js → flask_catalog.py → CatalogApi → BaseDataImportService → 领域对象 → Repository`。
当前导出由浏览器读取列表／机场详情后生成 JSON。本次将下载迁移到后端。

五个数据集：`airports`、`missions`、`aircraft_types`、`resource_types`、`aircraft_resource_requirements`。
模型位于 `backend/domain/{airport,airport_operations,mission,catalog}.py`，现有事务写入位于
`backend/storage/{airport_repository,mission_repository}.py`，保留领域模型及写入逻辑。

修改范围：新增 codec、文件导出服务、SM4 文件模块；扩展导入服务、Catalog API／Flask 路由、基础数据页面和文件上传客户端；新增 openpyxl 正式运行依赖和相关测试。

参考来源：`D:\Data\移植\AirportGroup_Project.zip` 中同名 codec、导入服务、Catalog API、Flask 路由、基础数据前端与 `backend/security/sm4.py`。
复用其格式分层、业务记录收集思路以及 SM4 常量／轮函数。改写 Excel 为 openpyxl，移除 Base64 传输与字段加密；SM4 改为文件整体 CBC 加密。

主要风险及验收：停机位／跑道摘要／保障配置必须完整回导；错误文件不得成为空数据替换；加密失败必须返回错误；CSRF、权限、草稿保护和页面卸载取消请求必须保持。

## 接口

- `POST /api/base-data/import-multi`：`multipart/form-data`，字段 `dataset`、`format`、`encrypted`（true/false，默认 false），以及唯一 `file` 上传。
- `GET /api/base-data/export?dataset=...&format=...&encrypt=false` 或 `POST /api/base-data/export` JSON 参数：返回真实附件字节。
- 导入要求 `catalog.write`；导出要求 `catalog.read`，保持原只读用户可导出的行为。
- POST 继续执行现有 CSRF guard；GET 导出为只读操作。记录安全摘要，不记录文件正文或密钥。
- 导出在单个只读数据库事务快照中复用现有 Repository 读取方法，避免并发修改导致机场／保障配置或任务／需求混合版本；编码和加密在读事务结束后进行。
- 旧 `POST /api/base-data/import` 保留。旧 JSON 显式 `items: []`、合法 CSV 仅表头的替换语义保留；错误表头不允许清库。
- 页面和新文件上传接口拒绝无记录文件，避免误清空。当前空数据集可以导出，其文件不能通过新上传接口清库。

## 文件结构

- JSON：`{"dataset": "...", "items": [...]}`。输入还支持数组、含 `items` 的对象（可带旧 `exported_at`）和 `airport_master_v1` 主文档。
- CSV：UTF-8（导出带 BOM），英文业务字段名；可选字段空白表示未填写。
- XLSX：标准 openpyxl 工作簿，单个工作表，首行为字段名。支持原生数字／布尔／文本；不执行公式，输入公式／错误单元格会报错。
- XML：`<records><record><字段名>值</字段名></record></records>`；`null="true"` 标记空值。根节点、记录结构及重复字段严格校验，不接受 DTD／实体。
- 表格列定义在 `TABULAR_COLUMNS`／`REQUIRED_COLUMNS`。必需列缺失、未知列、重复列、列数错误均报错。可选列可省略。
- 机场嵌套字段为 `runways_json`／`operational_profile_json`；任务需求为 `aircraft_requirements_json`。
- `runways_json=null` 表示跑道数据未知；`[]` 表示已知无跑道。数量和最大长度与结构化跑道保持一致。
- 停机位／时间窗为整数，拒绝小数和布尔值；稳定 ID 应以文本单元格保存。
- 限制：文件 50 MB，10 万条记录，100 列，Excel 解压后总大小 100 MB，单个 Excel 文本单元格最多 32767 字符。

## SM4 文件

管理员配置唯一环境变量 `AIRPORT_GROUP_SM4_KEY`，值为 32 个十六进制字符（16 字节）。不提供默认密钥，不写入代码或日志。

导出 `dataset-current.xlsx.sm4` 等附件。文件封装为版本标记 `AGSM4\x01`、16 字节随机 IV、SM4-CBC 密文、32 字节 HMAC-SHA256 标签。
认证密钥由同一个 SM4 密钥作用途隔离派生。解密前先验证完整性，错误密钥／截断／篡改均失败。
`.sm4` 扩展名、文件头或加密复选项触发解密。内层格式由格式选项指定。

这是一种本系统文件封装，不兼容参考工程的字段级密文。未配置密钥时明文导入／导出仍正常，加密操作明确报错。加密失败不下载明文。

## 验证命令

在项目根目录运行（使用项目 Python，测试数据库放在 `.tmp`）：

```powershell
$env:TMP = (Resolve-Path .tmp).Path
$env:TEMP = $env:TMP
.\.venv\Scripts\python.exe -B -m unittest tests.services.test_data_codec_service tests.services.test_sm4_file tests.services.test_base_data_multi_format tests.web.test_base_data_files_api tests.web.test_catalog_api tests.services.test_airport_seed_service
.\.venv\Scripts\python.exe -B -m pytest tests/frontend/test_base_data_files_browser.py tests/frontend/test_base_data_validation_browser.py -q -p no:cacheprovider --basetemp=.tmp/pytest-base-data-files
```

提交前必须核对：五数据集四格式业务字段回导一致；加密文件回导；空文件、缺列、XML 根错误、解密失败及关联约束失败均不改变数据库；浏览器下载／上传、只读权限、草稿保护和取消请求；Git diff 仅含本次授权增量。用户确认后再提交。

## 本次验收结果（2026-10-10）

- 后端、API、前端契约及现有仓储回归：60 项通过，包含五数据集四格式回导、SM4 标准向量／文件回导、错误文件数据库不变及并发导出快照一致性。
- 真实浏览器：12 项通过，包含 3 项新文件流程测试及 9 项现有基础数据编辑器测试，验证格式选择、真实下载／上传、xlsx.sm4、草稿保护、只读权限和页面卸载取消。
- 测试使用隔离数据库；未连接正式业务数据库。Git diff --check 通过。
- 部署须安装新增 openpyxl 依赖并配置加密环境变量。纯 Python SM4 对大文件耗时较长；当前未做大文件性能验收。单文件按数据集替换，跨数据集导入仍须遵守现有外键顺序。
- 未执行 Git 提交。本次独立增量补丁位于 `.tmp/base-data-multi-format.patch`，以开始实施时的工作区快照为基线，排除用户原有修改。
