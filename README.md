# GPredict <-> IC-9700 CI-V Proxy (Satellite Mode)

**Author : BH4FUO**  
**License : MIT License**

---

## 1. 项目概述

本项目是一个基于 Python + Tkinter 的 Windows GUI 小工具，用于实现 **GPredict 卫星跟踪软件** 与 **Icom IC-9700 电台** 的直接 CI-V 串口控制，完全绕过 Hamlib/rigctld 中间层。

### 核心文件

| 文件 | 说明 |
|------|------|
| `gpredict_civ_gui.py` | GUI 主程序，监听 GPredict 的 rigctld 协议，直接通过串口控制电台 |
| `civ.py` | IC-9700 CI-V 协议编解码库（频率 BCD 转换、帧打包/解析、串口读写） |
| `lan.py` | Icom LAN UDP 协议实现（wfview 兼容，用于网络音频场景） |

---

## 2. 实施目的

在业余卫星通信（Satellite QSO）中，使用 GPredict 进行卫星跟踪和多普勒修正时，需要将实时计算出的上行/下行频率同步到电台。传统方案通过 **Hamlib (rigctld)** 控制 IC-9700，但在卫星模式下存在严重的 VFO 映射问题，导致：

- Main 和 Sub 频率被覆盖成相同值
- 跨波段切换时命令被拒绝
- GPredict 的 Duplex TRX 配置无法正确区分 VFO

本项目旨在提供一个**稳定、直接、可控**的替代方案，彻底消除 Hamlib 层的 VFO 映射歧义。

---

## 3. 核心问题分析

### 3.1 Hamlib VFO 映射 Bug

Hamlib 4.7.1 在处理 IC-9700 卫星模式时：
- 当 `satmode=0` 时，VFO A/Main 都映射到 `vfo_number=0`，导致上行/下行命令互相覆盖
- 当 `satmode=1` 时，直接 `0x05` 频率设置会在跨波段时被电台拒绝（`Command rejected by the rig`）
- Hamlib 的 fallback（VFO swap）在 GPredict 高频轮询下不稳定

### 3.2 GPredict Device 2 = None 陷阱

GPredict 的 Duplex TRX 需要 Device 1 和 Device 2 同时配置。若 Device 2 留空，GPredict 会把上下行命令都发给同一个设备，导致频率覆盖。

### 3.3 VFO A/B Split 模式的显示限制

使用普通 VFO A/B + Split 模式虽然可以跨波段设置，但 wfview 的 Sub Band 无法实时显示 VFO-B 的频率（Split 模式下 VFO-B 只在发射时可见），不满足卫星操作中同时监控上下行的需求。

### 3.4 ISS 等 FM 卫星的亚音问题

FM 语音中继卫星（如 ISS、SO-50）需要上行携带 CTCSS 亚音。手动在电台菜单里设置容易搞反上行/下行，导致无法打开卫星中继。

---

## 4. 解决方案

### 4.1 架构设计

```
GPredict ──TCP:4532──> [本代理工具] ──Serial:COM16──> IC-9700
                            │
                            └── CI-V 协议直接控制
                            └── 自动卫星模式/波段适配/亚音设置
```

### 4.2 关键技术点

#### 4.2.1 直接 CI-V 控制，绕过 Hamlib

- 使用 `civ.py` 直接打包 CI-V 帧
- `0x07` 显式选择 VFO（Main=`0xD0`, Sub=`0xD1`）
- `0x05` 设置精确频率
- `0x03` 读取当前频率
- 完全自主控制，不依赖 Hamlib 的 VFO 推断逻辑

#### 4.2.2 ICOM 卫星模式 + 自动波段适配

启动时自动发送 `0x16 0x5A 0x01` 进入卫星模式。每次更新频率前：

1. 读取目标 VFO 当前频率，判断波段
2. 如果波段不匹配目标频率：
   - 若另一个 VFO 在目标波段 → 执行 `0x07 0xB0`（VFO Exchange）
   - 若都不在 → 先设置一个该波段中心频率（强制切换波段模块）
3. 再设置精确频率

#### 4.2.3 Swap Up/Down VFOs 选项

提供复选框切换映射方向：

| 模式 | Downlink (RX) | Uplink (TX) |
|------|---------------|-------------|
| 默认 | Main (UHF)    | Sub (VHF)   |
| Swap | Sub (UHF)     | Main (VHF)  |

适配不同操作者的习惯及电台菜单里 `TX Band` 的配置。

#### 4.2.4 自动上行亚音设置

通过 CI-V `0x1B` 命令设置 CTCSS 频率 + `0x16 0x42` 启用 Repeater Tone：

```
0x07 <uplink_vfo>          # 选上行 VFO
0x1B 0x00 <tone_bcd>       # 设置亚音频率（如 67.0 Hz）
0x16 0x42 0x01             # 启用亚音发射
```

支持 39 种标准 CTCSS 频率下拉选择。

---

## 5. 使用说明

### 5.1 环境准备

1. 关闭所有占用 COM16 和 4532 端口的程序（包括 rigctld）
2. 确认 IC-9700 的 CI-V 波特率为 115200（菜单：`SET → Connectors → CI-V Baud Rate`）
3. 确认电台卫星模式菜单里 `TX Band` 配置正确（通常为 Sub）

### 5.2 启动工具

```powershell
cd "D:\IC9700 CIV CTRL"
python gpredict_civ_gui.py
```

### 5.3 配置步骤

| 步骤 | 操作 |
|------|------|
| 1 | 选择串口（COM16）和波特率（115200） |
| 2 | 根据习惯勾选/取消 **Swap Up/Down VFOs** |
| 3 | 若通 FM 卫星，勾选 **"设置上行亚音"** 并选择频率 |
| 4 | 点击 **Start** |
| 5 | GPredict Radio Control 里 Device 1 选 `localhost:4532`，Device 2 选 `None` |
| 6 | 点击 **Engage**，然后选卫星点 **Track** |

### 5.4 验证

- wfview 里 Main Band 应显示下行频率（如 435~438 MHz）
- wfview 里 Sub Band 应显示上行频率（如 145 MHz）
- 日志里应看到 `Set Main = xxx MHz`、`Set Sub = xxx MHz`，且无 `0xFA` 错误

---

## 6. 文件清单

| 文件 | 说明 |
|------|------|
| `gpredict_civ_gui.py` | GUI 主程序，完整的 TCP→CI-V 代理逻辑 |
| `civ.py` | CI-V 协议底层（BCD 编解码、帧结构、串口线程） |
| `lan.py` | Icom LAN UDP 协议（wfview 网络音频场景备用） |
| `gp2hmlb.py` | 参考方案：DL7OAP 的 GPredict-Hamlib 中间层（已下载备用） |
| `app.py` | 项目原有的 Flask Web 控制界面 |
| `sat.py` | 卫星 TLE/轨道计算相关代码 |

---

## 7. 参考资料

1. **Icom IC-9700 CI-V Reference Guide**  
   Icom 官方 CI-V 协议文档，涵盖命令 `0x05`（设频）、`0x07`（选 VFO）、`0x16`（功能开关）、`0x1B`（亚音设置）等。

2. **Hamlib Source Code (v4.7.1)**  
   GitHub: https://github.com/Hamlib/Hamlib  
   参考 `icom.c` 中 `icom_set_vfo`、`icom_set_freq`、`icom_one_transaction` 的实现，理解 Hamlib 处理 IC-9700 卫星模式的 VFO 映射逻辑及 fallback 机制。

3. **gp2hmlb - GPredict to Hamlib Plugin (DL7OAP)**  
   GitHub: https://github.com/dl7oap/gp2hmlb  
   本项目的重要参考。gp2hmlb 在 GPredict 和 Hamlib 之间做中间层，显式切换 VFO 来解决 split 问题。本工具的核心启动序列和波段适配逻辑受其启发。

4. **wfview Icom LAN Protocol Documentation**  
   参考 wfview 社区对 Icom UDP 协议（端口 50001/50002）的逆向工程，用于 `lan.py` 中的认证、保活和 CI-V 隧道实现。

5. **GPredict Documentation - Radio & Rotator Interfaces**  
   GPredict Wiki: https://github.com/csete/gpredict/wiki  
   了解 GPredict 的 Duplex TRX 模式、rigctld 协议（`F`/`I`/`f`/`i` 命令）、Rotator 接口配置。

6. **ICOM Satellite Mode Operation Manual**  
   IC-9700 中文操作手册 / 英文高级手册（项目目录内 PDF），参考卫星模式的 VFO 分配、波段切换、`TX Band` 菜单设置。

---

## 8. 卫星多普勒软件跟踪（双 VFO 方案，2026-09 新增）

参照 CSN S.A.T. 硬件控制器的电台控制思路，纯软件实现多普勒自动跟踪，**彻底绕开 IC-9700 卫星模式的 CI-V 缺陷**（卫星模式下 `07 01` 返回 NG、`25/26` 命令返回 NG，VFO A/B 无法可靠控制）。

### 8.1 工作原理

- 电台保持**普通 VFO 模式**：启动时自动发送 `16 5A 00`（卫星模式 OFF）、`16 59 01`（Dualwatch 双收 ON）、`1A 05 0033 00`（SUB 发射静音 OFF，发射时可监听下行）
- **MAIN = 上行(TX)，SUB = 下行(RX)**：普通模式下 PTT 永远从 MAIN 发射，与 S.A.T. 手册"always use VFO mode"的建议一致
- 每个 VFO 独立控制：`07 D0/D1` 选择 MAIN/SUB → `05` 设频 / `06` 设模式 / `1B 00` + `16 42` 设上行亚音
- 多普勒模型（skyfield + TLE，`range_rate` 由 `frame_latlon_and_rates` 取得）：
  - 下行接收：`f_rx = f_down × (C − v) / C`
  - 上行预补偿：`f_tx = f_up × C / (C − v)`（符号与下行相反；旧 `sat.py` 两处同号，是错误的）
- SSB/CW 直线转发器：0.5 s 周期连续跟踪（10 Hz 步进）；FM 转发器：1 s 周期 + 量化步进（10 Hz / 100 Hz / 1 kHz / 2.5 kHz / 5 kHz 可选），避免静噪频繁启闭

### 8.2 操作功能（Web 卫星面板）

- CelesTrak TLE 一键更新（urllib 标准库，无额外依赖）或手动粘贴；**TLE 本地持久化**（`tle_cache.json`），超过 7 天未更新面板橙色提醒，可勾选"启动时自动更新"
- 观测点支持**梅登海格网格**（4/6/8 位，如 PM01PE）自动转经纬度
- **过境剖面图**：24h 内下一次过境的仰角-时间曲线（AOS/LOS/最高点/方位变化标注）
- 9 颗常用星预设（ISS/SO-50/AO-91/AO-85/FO-29/RS-44/CAS-4A/CAS-4B/AO-7），全部频率可改
- 24h 过境预测（AOS/LOS/最大仰角）、实时方位/仰角/距离率显示
- **LOCK VFO**：转动 SUB 拨盘微调下行时，软件周期性读取 SUB 频率并把变化镜像到上行（反转转发器自动取反）
- **CENTER** 一键清除手动偏移；上/下行 ±1 kHz 微调按钮；**更新 OFF** 暂停频率推送以便手动操作电台
- 微调下行建议使用电台 RIT 旋钮（S.A.T. 手册推荐的最佳实践）

### 8.3 实机踩坑记录（2026-09-14，远程 LAN 实测）

1. **MAIN/SUB 不允许同波段**（IC-9700 基本手册原文："The same band cannot be set to both Main and Sub bands"）。任何会让 MAIN 和 SUB 落到同一波段的 `0x05` 写频率都会被 `FA`(NG) 拒绝——首次实机时 MAIN=437.44(UHF)、SUB=144.46(VHF)，直接写 MAIN=145.99 / SUB=437.80 双双 NG。因此引擎在每次启动时先做**波段适配**：读回两个波段，波段对调时发 `07 B0` 交换，单侧不符时先写目标波段中心频率过渡，再写精确频率。
2. **回读校验**：首次推送频率后读回 MAIN/SUB 频率比对，不一致时在面板上显示红色错误（不再"盲发"）。
3. **wfview 音频来自 MAIN 接收机**。两条路线：
   - **全双工 QSO（推荐）**：常规布局 MAIN=上行、SUB=下行，wfview 的 RX Codec 选 **LPCM 2ch 16bit（立体声）**——Icom LAN 音频流两个声道分别携带 MAIN/SUB 音频（wfview 手册："Use a 2ch stream for radios that have dual VFOs"），用系统音量平衡只听 SUB 侧即可听到下行，PTT 从 MAIN 发射。引擎初始化时会把 Sub Band Mute (TX) 的喇叭（0033)、USB(0034)、LAN(0035) 三个输出全部关静音，保证发射时 LAN 音频里的 SUB 声道不断流。
   - **主收听模式（swap，纯收听）**：MAIN=下行（wfview 可听）、SUB=上行仅参考。⚠ 此模式下绝对不要按 PTT——普通 VFO 模式发射永远从 MAIN 发出，会打在卫星下行频率上。
4. `1B 00` 亚音写在 LAN 会话中出现过一次 NG（疑与 wfview 并发会话的命令交织有关），如亚音未生效可在面板上重新"开始跟踪"。
5. **端口冲突**：旧实例占用 8080 时新实例会自动向后找可用端口（8081、8082…），浏览器打开的是实际端口。
6. **退出恢复原状**（2026-09-14）：开始跟踪时快照卫星模式/Dualwatch/SUB 静音×3/双波段频率与模式/亚音频率与开关/RIT 状态；停止跟踪时完整还原（含波段防冲突过渡），不影响常规半双工操作。面板可关闭该行为（默认开启）。
7. **会话生命周期**（2026-09-14）：日志每次启动重写（mode="w"）；最后一个网页客户端断开 8 秒后后端自动退出并删除本次日志（刷新页面不会误触发；多开标签页需全部关闭才触发）。`--no-browser` 参数可禁止启动时自动打开浏览器（无人值守/远程场景）。

### 8.4 限制

- IC-9700 SUB 波段只有 144/430 MHz：**下行在 1.2 GHz 的卫星无法用此方案接收**（配置时会校验并提示）
- 预设频率为转发器中心频率，过境前应对照 SatNOGS/AMSAT 核实

### 8.4 新增文件

| 文件 | 说明 |
|------|------|
| `sat_tracker.py` | 跟踪引擎：TLE 获取、多普勒计算、CI-V 双 VFO 会话、LOCK VFO、过境预测 |
| `test_sat_tracker.py` | 离线测试：多普勒数值/符号、CI-V 帧序列（假电台）、JSON 序列化 |

---

## 9. 版权声明

Copyright (c) 2025 BH4FUO  
Released under the MIT License.

本工具为业余无线电爱好者开源项目，仅供个人学习和业余通信使用。  
ICOM、IC-9700、wfview、GPredict、Hamlib 等均为其各自所有者的商标。
