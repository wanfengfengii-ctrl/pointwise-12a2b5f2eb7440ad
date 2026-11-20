# 青屿泵业振动试验台

可靠性试验室用一台单通道设备测量泵体振动速度。班员安排测点和试验上限，操作员采录并读表，质检员核对峰值及出处后签发。夜班凭报告号领取同一份结论和回执。

需要 Python 3.11，使用标准库。在项目根目录运行。

```sh
python3 -B check_project.py
python3 -B demo.py
```

归档初始化不读取录制。演示从 `recordings/peak.json` 和 `recordings/empty.json` 馈送样本，在临时目录创建 SQLite 归档，并于关闭设备和首次连接后按号领取。项目包含完整的 unittest 套件，也可直接运行。

```sh
python3 -B -m unittest discover -s tests -t . -p 'test_*.py'
```

## 一次停机试验

1. 班员用 `ChannelProfiles.add(ChannelProfile(channel, label))` 建立测点。`jobs.enrollment.register(catalog, profiles, trial_id, channel, limit)` 登记一次试验。限值为非负整数 µm/s。非法登记抛出 `RegistrationError`，修正后可使用原号。重复登记保留原单。
2. `capture.control.start(catalog, sessions, device, trial_id)` 开始独占采样并返回会话号。会话固定本次测点与限值。未知或已用试验、设备占用、未清槽均抛出 `StartError`。失败的开测保留试验资格。
3. `DevicePort.sample(channel, value)` 接受读数并返回本设备的连续样本号。停止期间、通道错误、负数、布尔值和非整数输入抛出 `SampleRejectedError`。关闭的设备抛出 `DeviceRejectedError`。拒绝的读数不消耗样本号。录制是由 `device.playback.load_recording` 读取的 JSON 数组，例如 `[{"action":"sample","channel":"pump-body","value":4}]`。`run_playback` 按给定顺序执行录制。
4. `capture.preview.preview(sessions, device, session_id)` 可重复读取当前运行会话的不可变快照。`capture.stop.stop(sessions, device, session_id)` 先停写，再返回带设备起停控制事实的 `StoppedEvidence`。`require_stopped_evidence` 可重复读取当前停机原件。未知、错误状态或归属不符的会话抛出 `SessionStateError`。
5. `archive.issue.issue(store, sessions, device, session_id, evidence)` 对照可信停机原件核验送签内容，并按冻结阈值裁决。停机水位减开测水位为样本数。非空试验返回首末样本号、峰值与首次峰值号，空测的四个字段为空。峰值小于或等于限值为 `pass`，超限为 `fail`，空测为 `no_data`。`4, 9, 9` 在限值 9 下通过，峰值见证为 #2。
6. 报告与回执共同提交。送签资格或证据不符抛出 `IssueError`，事务故障抛出 `ArchiveCommitError`。事务故障后原槽和占用保持，原样重投可续办。已签同证据返回原报告和回执，变造重投报错。已签重投可给 `device` 传 `None`。
7. `capture.reset.reset(sessions, device, store, session_id)` 对照同次已提交的报告和回执，清槽并归还设备。资格不符抛出 `ResetError`。下一试验从空槽接续本设备样本号，已用试验保持已用。
8. `ArchiveStore.open(path)` 可独立打开或初始化归档。`archive.readback.read(store, report_id)` 返回固定的 `IssuedRecord`，其中 `report.evidence.original.snapshot` 是原计量，`receipt` 是原回执。未知号抛出 `ReportLookupError`。用完调用 `close()`。重复初始化保留已有合法成果。修改录制只影响随后显式馈送的新试验。

## 设备录制与源序

`TrialCatalog`、`ChannelProfiles`、`SessionRegistry` 和 `DevicePort` 由调用方在同一次台架工作进程中维护。登记在同一班员入口顺序提交，采集入口共享会话账。SQLite 连接由打开它的线程使用。报告号、会话号和设备身份是不可解释的标识，演示断言其关联和稳定性。

独立设备夹具可以调用 `read_slot`、`switch_write_slot` 和 `reset_idle_slot`，或在录制中使用 `read`、`switch` 和 `reset_idle` 步骤。切换只能指向空的停用槽。设备在同一源序中执行采样和控制，切换事件携带实际样本水位、旧槽和目标槽。写入槽与受保护的停机原槽不能由停用槽原语清除。`subscribe()` 返回实时订阅，`drain()` 按顺序领取自订阅以来尚未读取的样本与控制事件，关闭订阅时丢弃未领事件。设备的 `actions` 保留控制事实；读槽不产生控制事件。日常试验通过 `start`、`preview`、`stop`、`issue` 和 `reset` 完成。

完整回归覆盖同值峰、输入拒绝、忙态归属、停机事实核验、提交失败、已签重投、复位重用、断源领取、零峰和大整数。归档的提交前故障回归在真实 SQLite 事务内注入一次异常，并通过独立连接检查可见性。
