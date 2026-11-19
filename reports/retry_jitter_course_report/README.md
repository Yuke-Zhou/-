**课程研究报告及其来源**

作者：周宇科，学号22634015。报告更新于2026年11月19日。

- [报告提交版 PDF](https://github.com/Yuke-Zhou/-/blob/main/reports/retry_jitter_course_report/课程研究报告_周宇科_22634015.pdf)
- [可修改和版本管理的 Markdown](课程研究报告_周宇科_22634015.md)
- [基础实验与原始报告对话](../../conversations/full_dialogue.md)
- [部分共享实验与本次修订对话](../../conversations/partial_sharing_dialogue.md)
- [实验工程及运行方法](../../experiments/retry_jitter/README.md)
- [报告数据来源指纹](report_sources.json)

公开仓库：https://github.com/Yuke-Zhou/- 。首页链接与逐条对话入口已同步写入报告。

报告包含基础实验的1400轮数据，以及部分共享扩展实验的8800轮数据。第8节呈现共享比例与首次检出档位，第9节比较同步组规模和错峰启动，第10节说明两轮实验的贡献与协作分工。检出档位适用于当前模拟配置。

PDF共11页。Markdown的6张图位于assets目录。两份对话分别记录对应阶段的可见文本，导出时间和末条消息标识见各自的校验文件。

排版采用A4纸，正文宋体五号（10.5磅）、1.5倍行距，两端对齐，首行缩进两个汉字（21磅），段后6磅。标题和关键词不缩进；表题位于表上方、图题位于图下方，题注居中，参考文献采用悬挂缩进。Markdown正文使用两个全角空格保留段首缩进，小标题采用三级标题。

如需重新生成PDF，在已安装ReportLab且可用宋体、黑体的Windows环境中，从项目根目录运行：

```powershell
python reports/retry_jitter_course_report/build_report.py
```

构建器直接读取两轮冻结结果并核查文件指纹。报告正文和Markdown共用同一份内容源。
