# -
海洋数据挖掘技术课程作业，对上课的某个知识点进行拓展实验，总结报告

## 本次课程研究

**作者：周宇科　学号：22634015**

课题：同等每轮期望等待下，重试抖动幅度及客户端相关性对故障恢复的影响。

- [完整 AI 协作对话 Markdown](conversations/full_dialogue.md)
- [对话快照来源与逐条校验信息](conversations/export_metadata.json)
- [课程研究报告 PDF](reports/retry_jitter_course_report/课程研究报告_周宇科_22634015.pdf)
- [课程研究报告 Markdown](reports/retry_jitter_course_report/课程研究报告_周宇科_22634015.md)
- [实验工程与复现说明](experiments/retry_jitter/README.md)
- [正式实验结果摘要](experiments/retry_jitter/results/formal_v1/RESULTS_SUMMARY.md)

实验使用纯Python离散事件模拟，200个种子、1400轮、83000个客户端任务、577798次请求，保留完整模拟日志与校验指纹。15项检查已通过；验证程序已从原始日志重算指标。

高负载无抖动的平均未按期完成率为75.72%，中等与大幅独立抖动均为0%；共享抖动为73.94%。结论仅覆盖本次有限队列模型，不能推断真实服务零超时，也不能按本轮主指标选出中等与大幅的普遍优劣。研究贡献定位为受控实验与机制验证，没有宣称新算法。

对话保留最初机械臂方向的讨论、转题原因和后续全过程；导出文件注明快照截止位置。数据和图表由工程生成，报告首页有本公开仓库与对话入口。
