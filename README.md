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

## 8. 版权声明

Copyright (c) 2025 BH4FUO  
Released under the MIT License.

本工具为业余无线电爱好者开源项目，仅供个人学习和业余通信使用。  
ICOM、IC-9700、wfview、GPredict、Hamlib 等均为其各自所有者的商标。
