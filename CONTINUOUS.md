# 振动试验连续切段

现有停机试验、登记及读数输入见 `README.md`。连续模式面向单台设备上的单通道试验。控制请求由一个入口顺序提交，设备读数可在任意两次设备或归档操作之间到达。连续会话通过下列入口开测和切段，设备采样仍调用 `DevicePort.sample`。原始换槽和清槽接口属于设备原语，由采集端或独立原语测试调用。

## 开测、切段和领取

- `capture.control.start(catalog, sessions, device, trial_id, *, continuous=False)` 继续返回原会话号。`continuous=True` 从实际开测水位开始第一段，冻结试验通道和限值。省略参数沿用原停机试验。
- `capture.control.cut(store, sessions, device, session_id, request_id)` 供当前正在运行且设备匹配的连续会话使用。`request_id` 是会话内的非空字符串。未知、跨设备、普通停机会话或非法请求抛出 `SessionStateError`，不切段。返回值有 `status` 和 `issued` 属性。`status` 为 `issued` 时携带已签结果，`pending` 和 `busy` 时 `issued` 为空。
- `archive.readback.read(store, report_id)` 继续按报告号领取。切段的已签结果有 `report` 和 `receipt` 属性。`report` 提供 `report_id`、`session_id`、`channel`、`request_id`、`device_id`、`boundary`、`snapshot`、`limit` 和 `outcome`。`boundary` 是设备执行该切段时的真实 `ControlEvent`，含控制事件序和当时的 `last_sample_no`。`snapshot` 沿用 `RegisterSnapshot`，`outcome` 沿用 `LimitOutcome`，`receipt` 沿用 `Receipt` 的回执号、报告号和会话号。停源后重开归档，仍凭原报告号领取相同报告和回执。未知号沿用 `ReportLookupError`，原停机成果沿用原返回类型。

## 段边界及写入失败

前一边界是开测水位或最近一次实际切段水位。每段接纳此边界之后、截至本次设备切段水位的样本。后段从下一设备样本开始，采样不停。样本数等于两水位之差，非空段记录首末样本号、峰值与首次峰值原事件。峰值等于或低于冻结限值为 `pass`，超过为 `fail`。空切有独立切段事件和报告，计数为零，首末号、峰值、峰值样本号和峰值原事件均为空，裁决为 `no_data`。同一水位上的不同切段以实际控制事件区分。

一次只处理一项尚未签发的切段。接管提交前，采集端必须保有实际切点及原段计量的完整副本。签发只能使用已提交接管的同一原件，报告和回执同次提交后才可领取。接管或签发写入失败时返回 `pending`，仍接纳后段读数。同号重投沿原切点续办，已接管的原件直接供续签使用。在办期间，新号返回 `busy` 且不占号，已签请求复领原结果。已签重投与忙态均不得执行新的设备切段。连续模式可在最后一次签发后关闭源来核验交班。本次不涉及未签会话的跨进程恢复、多通道同步或连续会话的停机关单。

`ArchiveStore.open(path, *, before_commit=None, before_custody_commit=None)` 中的两个可选零参数钩子供确定性核验使用。`before_custody_commit` 在本段原件的接管事务提交前调用，`before_commit` 沿用原接口，并用于切段报告和回执的提交前。测试可在钩子中送入后段样本并抛出一次 `sqlite3.OperationalError`。两处失败均不发布当次报告与回执。接管失败仍能取得原段，签发失败留下已提交的原段接管证据。

## 录制和手算结果

新设备按限值 9 开测。用 `recordings/continuous.json` 的前三条读数采集，提交 A，在设备切段后送入剩余两条读数，再提交 B，紧接 B 提交 C。故障核验分别把其中一次接管或签发提交置于失败，观察同号续办和在办期间另一新号的状态。可用独立连接检查失败前后当次签发的可见性。切段期间没有停机动作。

| 请求 | 切段样本水位 | 样本数 | 首末样本号 | 峰值及首次样本号 | 裁决 |
| --- | ---: | ---: | --- | --- | --- |
| A | 3 | 3 | 1、3 | 9、2 | pass |
| B | 5 | 2 | 4、5 | 12、4 | fail |
| C | 5 | 0 | 空 | 空 | no_data |

## 运行

在项目根目录沿 `.pointwise-runtime.json` 的现有运行约定交付。清单保留版本 `1` 和 `cli` 类型，`setup` 是 argv 数组的列表，`verify` 和 `demo` 各是一条可执行的 argv。初始清单运行既有停机回归和演示。扩展后让核验包含原套件及切段联合检查，业务断言失败须退出非零。演示实际执行后段保峰、失败续办、关闭设备与重开归档，显示原报告和回执。
