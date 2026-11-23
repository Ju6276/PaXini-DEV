# Third-party notices / 第三方组件说明

## paxini-sdk

- Source / 来源: <https://github.com/Jingyi-Z/paxini-sdk>
- Upstream commit / 上游提交: `79c82145f88e158dd7706736e8c07f14f11cbd7a`
- Location / 本地位置: `vendor/paxini_sdk/`
- License / 许可证: MIT; see [the preserved license](vendor/PAXINI_SDK_LICENSE).

The vendored SDK provides tactile-board communication and force decoding. Its Python source and coordinate assets are included with the upstream license. It is a community SDK for PX6AX GEN3; the PX6D USB driver in `server.py` is implemented separately.

该 SDK 用于触觉通讯板的连接和力数据解析，Python 源码及坐标资源保留上游 MIT 许可证。它是面向 PX6AX GEN3 的社区 SDK；`server.py` 中的 PX6D USB 驱动另行实现。

## Runtime dependencies / 运行依赖

Python dependencies are declared in [requirements.txt](requirements.txt) and installed into a local virtual environment. Their source and installed packages are not copied into this repository.

Python 依赖声明于 [requirements.txt](requirements.txt)，安装在本地虚拟环境中，未随本仓库复制分发。

Vendor manuals and installers downloaded during device diagnosis remain local and are excluded from Git. The README documents their official download source.

诊断时下载的厂商手册及安装包仅保存在本机，不纳入 Git；README 提供官方下载来源。
