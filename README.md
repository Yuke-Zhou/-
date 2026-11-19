# -
海洋数据挖掘技术课程作业，对上课的某个知识点进行拓展实验，总结报告

## 本次课程研究

**作者：周宇科　学号：22634015**

课题：同等每轮期望等待下，重试抖动幅度及客户端相关性对故障恢复的影响。

- [基础实验与原始报告对话](conversations/full_dialogue.md) · [校验信息](conversations/export_metadata.json)
- [部分共享实验与本次修订对话](conversations/partial_sharing_dialogue.md) · [校验信息](conversations/partial_sharing_export_metadata.json)
- [课程研究报告 PDF](reports/retry_jitter_course_report/课程研究报告_周宇科_22634015.pdf)
- [课程研究报告 Markdown](reports/retry_jitter_course_report/课程研究报告_周宇科_22634015.md)
- [实验工程与复现说明](experiments/retry_jitter/README.md)
- [基础实验结果摘要](experiments/retry_jitter/results/formal_v1/RESULTS_SUMMARY.md)
- [扩展实验协议](experiments/retry_jitter/EXPERIMENT_PLAN_PARTIAL_SHARING.md) · [各档统计与图表](experiments/retry_jitter/results/partial_sharing_v1)

实验使用纯Python离散事件模拟，200个种子、1400轮、83000个客户端任务、577798次请求，保留完整模拟日志与校验指纹。15项检查已通过；验证程序已从原始日志重算指标。

高负载无抖动的平均未按期完成率为75.72%，中等与大幅独立抖动均为0%；共享抖动为73.94%。结论仅覆盖本次有限队列模型，不能推断真实服务零超时，也不能按本轮主指标选出中等与大幅的普遍优劣。研究贡献定位为受控实验与机制验证，没有宣称新算法。

扩展实验在200个新种子下运行8800轮。首次检出未完成率上升的档位从同时启动的20%推迟到错峰启动的70%。同样有50台参与共享时，一个50人组与五个10人组的未完成率分别为28.23%和0.42%。这些结果基于同一模拟队列与12秒截止时间。

11页修订报告的首页提供两段协作对话入口。两份Markdown保留各自会话的可见文本和快照截止位置。
