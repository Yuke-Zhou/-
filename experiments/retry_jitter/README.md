**随机重试抖动实验工程**

**部分共享扩展（2026-11-19）** 对100台客户端按5个百分点扫描共享比例，比较同步与错峰启动，并比较同为50台共享时的1组×50人与5组×10人。正式运行共8800轮。[实验协议](EXPERIMENT_PLAN_PARTIAL_SHARING.md) · [各档统计](results/partial_sharing_v1/by_profile.csv) · [共享比例曲线](results/partial_sharing_v1/figures/sharing_thresholds.png) · [分组和错峰对照](results/partial_sharing_v1/figures/sharing_mechanisms.png)

```powershell
python run_partial_sharing.py --output results/my_shared_run
python plot_partial_sharing.py --run results/my_shared_run
```

模拟脚本使用标准库，绘图使用Matplotlib。结果目录中的 `trials.csv` 保存逐轮指标，`scenarios.jsonl` 保存各个种子的恢复时刻、分组顺序和错峰时刻，`example_attempts.jsonl.gz` 保存种子20000的逐请求日志。基础实验如下。

对应《海洋数据挖掘技术》的指数退避、随机抖动与重试风暴知识点。目标是检验：每轮期望等待相同，中等独立抖动能否降低高负载未按期完成率，以及收益是否主要来自请求去同步。

本工程提供模拟器、冻结协议、原始日志、配对统计、机制消融、验证检查和图表。它不是课程实验报告，也没有把已有随机退避称为新算法。课程要求的完整AI对话还需真实导出；本工程不会伪造聊天记录。

先阅读 [实验协议](EXPERIMENT_PLAN.md) 与 [实现约定](IMPLEMENTATION_NOTES.md)。后续实际执行状态以结果目录的manifest.json为准，保留协议草案冻结时的原文状态。

核心模拟、统计和测试仅使用Python标准库，支持Python 3.7及以上；建议统一使用Python 3.10及以上，便于绘图。Matplotlib是可选绘图依赖，不参与生成模拟数据或推断。所有命令在本工程目录执行。

先检查解释器并运行验证检查：

```powershell
python --version
python -m unittest discover -s tests -v
```

先做小规模验证（参数覆盖写入结果，不能称为正式200种子实验）：

```powershell
python run_experiment.py --seeds 3 --bootstrap 200 --output results/smoke
python verify_run.py --run results/smoke
```

按固定配置运行正式实验：

```powershell
python run_experiment.py --output results/my_formal_run
python verify_run.py --run results/my_formal_run
```

输出目录不得已存在非空内容。重跑使用新目录，避免覆盖证据。默认配置为200个种子、每种子7个试验：低负载3个策略，高负载3个策略加共享抖动消融，共1400轮。随机种子从10000开始；Bootstrap为10000次。

若工程包附有 results/formal_v1，可以直接先查看其中的RESULTS_SUMMARY.md和figures，不必安装绘图依赖或重跑。该摘要由数据自动生成，不是最终课程报告。

用Python 3.10或以上安装可选绘图依赖，再从结果生成PNG与SVG：

```powershell
python -m pip install -r requirements-plots.txt
python plot_results.py --run results/my_formal_run
```

如figures已存在，使用 --output figures/my_new_figures 指向新目录。图表采用英文标注避免不同电脑的中文字体缺失；None=无抖动，Medium=中等，Large=大幅，Independent=客户端独立随机，Shared=同轮共享随机。01是未完成率，02是配对效应区间，03是机制消融，04是预先选定首个种子的时间线。时间线的固定分桶仅用于展示；正式峰值使用滑动窗口。

无需重跑模拟，也可以从trials.csv重新计算统计：

```powershell
python analysis.py --run results/my_formal_run --output results/recomputed_analysis.json
```

上述输出文件须为新文件。不要通过编辑JSON/CSV修改结果；若发现程序错误，记录修复并完整重跑。

| 文件 | 内容 |
|---|---|
| config.json | 固定主参数和策略 |
| simulator.py | 整数纳秒离散事件模拟器 |
| run_experiment.py | 主入口，冻结配置和代码指纹，导出全部数据 |
| analysis.py | 种子级配对Bootstrap与数据摘要 |
| verify_run.py | 文件校验及从原始日志独立重算指标 |
| plot_results.py | 从导出数据绘图，不另跑模拟 |
| tests/test_simulator.py | 边界、计时、队列、随机相关性和配对统计检查 |
| results/.../effective_config.json | 该次实际配置，含命令行覆盖 |
| results/.../protocol_snapshot.json | 首轮结果产生前冻结的配置与指纹 |
| results/.../scenarios.jsonl.gz | 恢复时刻、客户端优先级和完整随机数表 |
| results/.../attempts.csv.gz | 每次请求、接纳/拒绝、响应、等待、下一次尝试 |
| results/.../clients.csv.gz | 每客户端尝试次数、成功时刻与最终状态 |
| results/.../trials.csv | 每轮指标，主要统计输入 |
| results/.../analysis.json | 汇总及配对置信区间 |
| results/.../manifest.json | 配置、代码和数据SHA-256、解释器及运行耗时 |
| results/.../figures/figures_manifest.json | 图表输入与输出指纹 |

日志中的时间字段以 _ns 结尾，单位为纳秒；除以10的9次方得到模拟秒数。空时间表示截止前未观察到该事件，不代表0秒。未完成包含截止前仍在排队、处理、等失败响应、等待下一次重试，或提前耗尽尝试次数。

主指标 unfinished_rate = 未按期完成人数 / 全部人数。total_attempts包含首次请求和失败请求。peak_retry_count为恢复后任意100毫秒窗口内的重试数。T95不在成功子集上计算；截止前未达成95%成功时使用删失标记。

H1_high_improvement = 高负载无抖动未完成率 - 中等独立抖动未完成率；正值支持改善。H2_load_interaction再减去对应低负载改善。比例差乘100才是百分点。三个mechanism差值均为共享减独立，正值表示独立抖动更好；分别看未完成率、峰值、队列满拒绝，不能只看其中一个就宣称机制已证实。

每轮期望等待上限是2秒，a=1的实际等待可达4秒。所有任务的共同截止时刻是12秒，不能每次失败后重新计时。所有策略的平均等待按失败轮次匹配，实际总等待和请求次数不强制相同。

本工程的服务是单工作器、5个等待位；拒绝请求不消耗处理器资源；所有客户端同步首次请求。结论只覆盖这一受控模型。低负载可能出现全成功的地板效应，不能据此证明固定等待普遍最优。真实网络、异步启动、大队列、响应丢失和自适应策略均不包含在本轮主实验中。

Windows若python命令指向已有旧环境，可使用某个Python解释器的完整路径运行这些脚本。不要为本实验改动已有研究环境；绘图可以另建环境。工程包不绑定开发时机器的绝对解释器路径。
