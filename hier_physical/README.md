# hier_physical — 分块硬化与层级顶层（H2，claude-opus-h2）

- `split.py`：整图切成 rom（16 地址锁存器 + 纯表锥）/ rest 两宏 + 顶层，逐记录核对重拼。
- `subtable.py`：把 9 位 E8 表（42,168 NAND，1,043 字）按字切成 K 块子表，每块复制地址锁存器与所需共享译码门（K=32 时 +62% 门），全部 512 地址穷举向量。
- `flow.py` + `.github/workflows/hier_layout.yaml`：Actions 上签核子表宏（DRC/LVS/XOR、九角、后布线穷举回放）。
- m64 GRT 扫描结果与整机修正见主仓 `h3/HIER.md`。本机不跑 EDA。
