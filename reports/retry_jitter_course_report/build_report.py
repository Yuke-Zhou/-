"""Build the course report from the frozen baseline and partial-sharing results."""
from pathlib import Path
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import csv
import hashlib
import html
import json
import re
import shutil

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                               Image, PageBreak, KeepTogether)
from reportlab.platypus import paragraph as paragraph_module


NO_LINE_START = set('，。！？；：、）》」』】〕〉］｝’”,.!?;:)]}%％‰')
NO_LINE_END = set('（《「『【〔〈［｛‘“([{')
# The library's CJK defaults omit several Chinese punctuation characters.
paragraph_module.ALL_CANNOT_START += ''.join(sorted(NO_LINE_START))


def chinese_lines(text, widths, font_name, font_size):
    """Wrap plain Chinese prose without separating punctuation or Latin words."""
    if not isinstance(widths, (list, tuple)):
        widths = [widths]
    units, prefix = [], ''
    for token in re.findall(r'[A-Za-z0-9_]+(?:[./-][A-Za-z0-9_]+)*| +|.', text):
        if token in NO_LINE_END:
            prefix += token
        elif token in NO_LINE_START and units:
            units[-1] += prefix + token
            prefix = ''
        else:
            units.append(prefix + token)
            prefix = ''
    if prefix:
        units.append(prefix)
    lines, row = [], ''
    for unit in units:
        available = widths[min(len(lines), len(widths) - 1)]
        if not row and unit.isspace():
            continue
        candidate = row + unit
        if row and pdfmetrics.stringWidth(candidate, font_name, font_size) > available + 1e-7:
            line = row.rstrip()
            lines.append((available - pdfmetrics.stringWidth(line, font_name, font_size), [line]))
            row = unit.lstrip()
        else:
            row = candidate
        available = widths[min(len(lines), len(widths) - 1)]
        if pdfmetrics.stringWidth(row, font_name, font_size) > available + 1e-7:
            raise ValueError('Unbreakable text exceeds report column: ' + row)
    if row:
        available = widths[min(len(lines), len(widths) - 1)]
        lines.append((available - pdfmetrics.stringWidth(row, font_name, font_size), [row]))
    return lines


class ReportParagraph(Paragraph):
    def breakLinesCJK(self, maxWidths):
        broken = super().breakLinesCJK(maxWidths)
        if (broken.kind == 0 and len(self.frags) == 1
                and hasattr(self.frags[0], 'text') and not getattr(self, '_splitpara', False)):
            fragment = self.frags[0]
            broken.lines = chinese_lines(fragment.text, maxWidths, fragment.fontName, fragment.fontSize)
        return broken


Paragraph = ReportParagraph

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
ENGINEERING = WORKSPACE / 'experiments' / 'retry_jitter'
RUN = ENGINEERING / 'results' / 'formal_v1'
EXTENSION = ENGINEERING / 'results' / 'partial_sharing_v1'
ASSETS = HERE / 'assets'
ASSETS.mkdir(exist_ok=True)
REPOSITORY_URL = 'https://github.com/Yuke-Zhou/-'
DIALOGUE_URL = REPOSITORY_URL + '/blob/main/conversations/full_dialogue.md'
EXTENSION_DIALOGUE_URL = REPOSITORY_URL + '/blob/main/conversations/partial_sharing_dialogue.md'
EXTENSION_RESULTS_URL = REPOSITORY_URL + '/tree/main/experiments/retry_jitter/results/partial_sharing_v1'
REPORT_DATE = '2026-11-19'
BODY_FONT_SIZE = 10.5
BODY_LEADING = BODY_FONT_SIZE * 1.5
BODY_FIRST_LINE_INDENT = BODY_FONT_SIZE * 2
# ReportLab's default frame has six-point padding on both sides.
CONTENT_WIDTH = 595.276 - 52.6 * 2 - 12


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


analysis = json.loads((RUN / 'analysis.json').read_text(encoding='utf-8'))
manifest = json.loads((RUN / 'manifest.json').read_text(encoding='utf-8'))
config = json.loads((RUN / 'effective_config.json').read_text(encoding='utf-8'))
for name, expected in manifest['artifacts_sha256'].items():
    if digest(RUN / name) != expected:
        raise ValueError('Changed experiment artifact: ' + name)
fig_manifest = json.loads((RUN / 'figures' / 'figures_manifest.json').read_text(encoding='utf-8'))
for name, expected in fig_manifest['figure_sha256'].items():
    if digest(RUN / 'figures' / name) != expected:
        raise ValueError('Changed figure: ' + name)
for name, expected in fig_manifest['input_sha256'].items():
    if digest(RUN / name) != expected:
        raise ValueError('Figure input differs from formal data: ' + name)
if analysis['trial_count'] != 1400 or analysis['seed_count'] != 200:
    raise ValueError('The baseline results must match formal_v1')

extension_manifest = json.loads((EXTENSION / 'manifest.json').read_text(encoding='utf-8'))
for name, expected in extension_manifest['outputs_sha256'].items():
    if digest(EXTENSION / name) != expected:
        raise ValueError('Changed partial-sharing artifact: ' + name)
for name, expected in extension_manifest['inputs_sha256'].items():
    if digest(ENGINEERING / name) != expected:
        raise ValueError('Changed partial-sharing input: ' + name)
extension = json.loads((EXTENSION / 'analysis.json').read_text(encoding='utf-8'))
if extension['trial_count'] != 8800 or extension['seed_count'] != 200:
    raise ValueError('The extension results must match partial_sharing_v1')
extension_groups = {(r['launch'], r['profile']): r for r in extension['groups']}
extension_thresholds = extension['thresholds']

g = {(r['load'], r['strategy']): r for r in analysis['groups']}
contrasts = analysis['contrasts']
high_base = g['high', 'no_jitter']
high_med = g['high', 'medium_independent']
high_large = g['high', 'large_independent']
high_shared = g['high', 'medium_shared']
if high_med['mean_unfinished_rate'] != 0 or high_large['mean_unfinished_rate'] != 0:
    raise ValueError('Report interpretation must be revised for changed outcomes')
with (RUN / 'trials.csv').open(encoding='utf-8', newline='') as handle:
    trials = list(csv.DictReader(handle))
no_jitter = [r for r in trials if r['load'] == 'high' and r['strategy'] == 'no_jitter']
fixed_counts = Counter(int(r['success_count']) for r in no_jitter)
attempt_count = sum(int(r['total_attempts']) for r in trials)
client_count = sum(int(r['clients']) for r in trials)
request_reduction = 100 * (high_base['mean_total_attempts'] - high_med['mean_total_attempts']) / high_base['mean_total_attempts']
queue_reduction = 100 * (high_base['mean_queue_rejections'] - high_med['mean_queue_rejections']) / high_base['mean_queue_rejections']


def pct(value):
    return f'{100 * value:.2f}%'


def ci_pp(name):
    r = contrasts[name]
    return f"[{r['ci95_low']*100:.2f}, {r['ci95_high']*100:.2f}]"


def extension_pp(proportion):
    points = Decimal(format(proportion * 100, '.12g'))
    return str(points.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def extension_ci(lower, upper):
    return f'[{extension_pp(lower)}, {extension_pp(upper)}]'


pages = []


def page(title):
    blocks = []
    pages.append((title, blocks))
    return blocks


def p(blocks, text):
    blocks.append(('p', text))


def lead(blocks, text):
    blocks.append(('lead', text))


def formula(blocks, text):
    blocks.append(('formula', text))


def table(blocks, headers, rows, widths):
    titles = ['均值匹配的策略定义', '服务和客户端的共同配置', '主实验的描述性统计',
              '预先指定的配对效应及置信区间', '高负载下独立随机与共享随机的机制对照',
              '部分共享实验的首次检出档位', '共享人数相同条件下的关键对照', '研究者和AI的实际分工']
    number = 1 + sum(1 for _, page_blocks in pages for item in page_blocks if item[0] == 'table')
    blocks.append(('caption', f'表{number} {titles[number-1]}'))
    blocks.append(('table', headers, rows, widths))


def figure(blocks, name, caption, width=490, run=RUN):
    shutil.copy2(run / 'figures' / (name + '.png'), ASSETS / (name + '.png'))
    blocks.append(('figure', name + '.png', caption, width))


b = page('研究问题与主要发现')
b.append(('cover', None))
lead(b, '摘要')
p(b, f'本文以海温查询服务的故障恢复为背景，用离散事件模拟分析重试抖动。基础实验共1400轮，在每轮期望等待相同的条件下，高负载无抖动的平均未完成率为{pct(high_base["mean_unfinished_rate"])}，中等独立抖动为0.00%，完全共享抖动为{pct(high_shared["mean_unfinished_rate"])}。扩展实验共{extension["trial_count"]:,}轮，固定单台客户端的等待分布，比较部分共享、组规模与启动时序。同时启动时，未完成率上升首次在{extension_thresholds["synchronous"]["first_detected_positive_delta"]}%共享档检出，首次有证据超过5个百分点的档位为{extension_thresholds["synchronous"]["first_detected_practical_delta"]}%。首次请求在1秒内错峰后，两档分别推迟至{extension_thresholds["staggered"]["first_detected_positive_delta"]}%和{extension_thresholds["staggered"]["first_detected_practical_delta"]}%。同为50台共享时，一个50人组与五个10人组的未完成率分别为{pct(extension_groups["synchronous","shared_050"]["mean_unfinished_rate"])}和{pct(extension_groups["synchronous","shared_050_split"]["mean_unfinished_rate"])}。结果表明，同步组规模和启动时序会改变本模型中的恢复表现。')
b.append(('keywords', '关键词：指数退避、随机抖动、同步组、错峰启动、有限队列、配对实验'))
lead(b, '选题来源')
p(b, '课程课件《让重复迟到失败都变得无害》第9—10页介绍了指数退避与随机等待：客户端在失败后增加等待，并通过不同的随机等待分散同步请求[1]。本研究将这一小知识点放入一个容量和队列均受限的海温查询接口模型中。接口是只读操作，海温服务仅提供应用背景；实验不使用真实海温观测，也不评估海洋温度本身的变化。')
p(b, '研究者最初判断，在客户端少、服务容量充足时，确定性等待可能已经足够，并明确把“尽可能减少超时的客户端数量”作为优先目标。本文将其转化为低负载对照与截止时间指标。这里“未按期完成”还包括提前用尽重试次数的任务，不只包含网络请求超时。')

b = page('已有方法与本项目的探索设计')
p(b, '随机退避不是本文提出的新算法。Brooker讨论了无随机性的指数退避仍可能形成请求簇，并比较了多种抖动方法[2]。Amazon Builders’ Library强调，重试会增加下游负载，随机等待可分散突发到达[3]。这些资料构成既有方法背景，不能被列作本文的新发现。')
lead(b, '控制平均等待而改变随机幅度')
p(b, '直接扩大均匀随机等待的上限，会同时改变平均等待和方差，难以区分收益来自等待更久还是请求更分散。本文采用均值匹配的参数化定义。第j次失败后，基准等待为：')
formula(b, 'b<sub>j</sub> = min(0.25 × 2<super>j-1</super>, 2) 秒，j = 1, 2, …')
formula(b, 'W<sub>i,j</sub>(a) = b<sub>j</sub> [1 + a(2U<sub>i,j</sub> - 1)]，U<sub>i,j</sub> ～ Uniform(0, 1)')
formula(b, 'E[W<sub>i,j</sub>(a)] = b<sub>j</sub>，Var[W<sub>i,j</sub>(a)] = a<super>2</super>b<sub>j</sub><super>2</super> / 3')
p(b, '由于2U-1的均值为0、方差为1/3，上式在相同失败次数下保持期望等待不变，只改变随机幅度。它不要求不同策略的实际总等待相同；成功时刻和尝试次数的变化本身就是结果。2秒是基准期望等待的上限，大幅抖动单次实际等待可达4秒。')
table(b, ['策略', '幅度a', '该轮等待分布', '期望等待'], [
    ['无抖动', '0', '固定 b_j', 'b_j'],
    ['中等独立抖动', '0.5', 'Uniform(0.5b_j, 1.5b_j)', 'b_j'],
    ['大幅独立抖动', '1', 'Uniform(0, 2b_j)', 'b_j']], [105, 48, 236, 101])
p(b, '本实验的无抖动是确定性指数退避，并不是“每次固定1秒”；1秒只曾作为解释平均等待的例子。均值匹配的大幅抖动也不能与使用相同上限的标准Full Jitter直接混为一谈。')
lead(b, '预先指定的两个主假设')
p(b, 'H1：高负载下，中等独立抖动的平均未完成率低于无抖动。H2：中等独立抖动相对无抖动的改善，在高负载下大于低负载。大幅抖动为预先安排的探索对照，不在看到结果后替换主比较。')
p(b, '本项目的探索内容由三部分组成：隔离平均等待这一混杂因素；根据研究者偏好固定客户端截止时间指标；通过“独立随机—共享随机”对照检验客户端相关性。这是具体实验设计和机制分析的贡献，尚未确认为具有学术方法新颖性的新算法。')

b = page('实验模型与评价口径')
table(b, ['配置项', '统一设置'], [
    ['负载', '低负载5个客户端；高负载100个客户端；每个客户端一个任务'],
    ['首次请求与恢复', '所有客户端在t=0发起；恢复时刻R每轮采自Uniform(2,4)秒'],
    ['服务能力', '单工作器，每次成功服务0.05秒，处理能力约20个/秒'],
    ['排队', 'FIFO，最多5个等待请求，另有1个可正在处理'],
    ['失败', '服务未恢复或队列已满时拒绝；0.02秒后收到失败响应'],
    ['已接纳请求', '排队与处理期间不重试；完成后接收成功响应'],
    ['任务预算', '共同截止时间t=12秒；最多20次尝试，包含首次请求'],
    ['客户端信息', '只使用自己的失败轮次、响应和随机数，不读取未来恢复时刻'],
    ['重复与规模', '种子10000—10199；主试验1200轮，机制对照200轮'],
    ['计时', '整数纳秒；截止时刻完成计成功，截止时刻不再发新请求']], [106, 384])
p(b, '故障期间请求不会在服务端积压。客户端下一次尝试时刻等于失败响应到达时刻加等待时间。同一时刻先处理完成事件，再处理失败响应和新到达；同时到达的请求按预生成优先级处理。截止前仍在处理、排队、等待响应或重试，以及提前耗尽次数的任务，均计为未完成。')
p(b, '100个请求的理想处理时间为5秒，即使第4秒才恢复，理论上也可在第9秒完成，早于12秒截止。因此测试并非由物理容量不足强制导致失败。')
formula(b, 'r = 截止前未成功的客户端数 / 本轮全部客户端数')
p(b, 'r为主指标。次指标包括全部请求次数、因队列满导致的拒绝次数、恢复后任意连续100毫秒内的重试峰值，以及达到全体客户端95%成功所需时间T95。峰值不含首次请求；T95在截止前未达成时记为“未达成”，不能只在成功客户端中计算P95。对于5个客户端，95%需向上取整为5个成功。')
p(b, '服务模型忽略拒绝请求消耗的CPU和网络资源，不设置单次请求超时，不模拟响应丢失或重复执行副作用。因此，指标反映的是有限队列模型中的任务可用性，而非真实HTTP服务的完整性能。')

b = page('主实验结果')
rows = []
names = [('no_jitter', '无抖动'), ('medium_independent', '中等独立'), ('large_independent', '大幅独立')]
for load, label in [('low', '低'), ('high', '高')]:
    for key, name in names:
        r = g[load, key]
        t95 = f'{r["t95_mean_among_reached_only"]:.3f}秒' if r['t95_reached_trials'] else '未达成'
        rows.append([label, name, pct(r['mean_unfinished_rate']), f'{r["mean_total_attempts"]:.2f}',
                     f'{r["mean_peak_retry_count"]:.2f}', t95])
table(b, ['负载', '策略', '未完成率', '请求量', '重试峰值', '平均T95'], rows, [36, 90, 80, 80, 80, 124])
p(b, '各组均为200轮均值。请求量为每轮全部尝试次数；峰值单位为次/100毫秒。除高负载无抖动0/200轮达到T95外，其余各组均为200/200轮达到，因此表中有数值的T95没有额外删除未达成试验。')
figure(b, '01_unfinished_rate', '图1 不同负载和幅度下的未完成率。横轴None、Medium、Large分别对应无、中等和大幅独立抖动。纵轴为截止前未完成客户端的比例。')
p(b, f'高负载下，中等独立抖动使未完成率从75.72%降至本次样本中的0%，每轮平均少75.72个未完成任务；请求量从842.88降至577.11，减少约{request_reduction:.2f}%。这是同时改善主指标和请求成本的结果，而非以更多超时换取更少请求。')
p(b, '低负载三组全部按期完成。确定性指数退避的平均T95为4.322秒，中等独立为4.993秒，大幅独立为5.579秒。因此本配置未观察到低负载的成功率收益，次指标上确定性等待也并不更差。该结果仍受12秒截止时间和当前恢复分布约束。')
p(b, '高负载中等和大幅独立抖动的主指标均为0%。大幅的峰值稍低，中等的平均T95稍短；这些属于描述性比较，未进行新的预设配对检验，不能据此声称某一幅度显著更优。')

b = page('配对统计与假设判断')
p(b, '每个种子预生成环境、客户端优先级和按“客户端×失败次数”索引的随机数。不同幅度复用这些输入，环境与重试随机流分开。它避免因策略消耗随机数的顺序不同而改变服务恢复时刻，并使比较在同一环境下配对。')
formula(b, 'd<sub>H,s</sub> = r<sub>s</sub>(0,H) - r<sub>s</sub>(0.5,H)')
formula(b, 'I<sub>s</sub> = d<sub>H,s</sub> - [r<sub>s</sub>(0,L) - r<sub>s</sub>(0.5,L)]')
p(b, '以200个独立种子为重采样单元，进行10000次配对百分位Bootstrap，得到平均差的95%区间。没有把同一轮客户端或577798次请求当成独立样本。5个百分点是协议预设的实际效果参考门槛，不是行业标准。区间描述固定模型与环境分布下的随机试验变化，不覆盖模型失真。')
table(b, ['比较', '平均改善', '配对95%区间', '本轮判断'], [
    ['H1 高负载', '75.72个百分点', ci_pp('H1_high_improvement'), '支持方向'],
    ['低负载', '0.00个百分点', '[0.00, 0.00]', '观察到打平'],
    ['H2 高减低', '75.72个百分点', ci_pp('H2_load_interaction'), '支持方向']], [124, 115, 136, 115])
figure(b, '02_paired_effects', '图2 配对效应与95%区间，横轴单位为百分点。区间较窄，在全量横轴下接近点状；准确数值以表4与analysis.json为准。')
p(b, 'H1区间下限大于0，且改善幅度超过预设5个百分点参考门槛，故在本模型内支持高负载收益。H2同样得到正向结果，但低负载改善恒为0，使H2在本次数据上与H1数值完全相同。二者不能视为两份独立的现实证据。')
p(b, '低负载区间[0,0]只是该有限样本中所有组都成功形成的结果，不等价于证明策略在所有期限、恢复相位和容量下无差异。中等与大幅独立组观察到零失败，也不能被解释为真实未完成概率必为零。')

b = page('共享随机等待的机制实验')
p(b, '若只比较随机与不随机，难以判断收益究竟来自单个客户端等待的波动，还是来自不同客户端不再同时请求。机制实验在高负载、a=0.5时，只改变随机数的客户端间关系：独立组使用U<sub>i,j</sub>，共享组在同一失败轮次使用同一个U<sub>j</sub>。两组单个客户端该轮的期望和边际分布相同，共享组则保留同步重试。')
table(b, ['指标', '中等独立', '中等共享', '共享减独立'], [
    ['未完成率', '0.00%', '73.94%', '73.94个百分点'],
    ['峰值 次/100毫秒', '11.085', '100.000', '88.915'],
    ['队列满拒绝 次/轮', '61.995', '371.010', '309.015'],
    ['全部请求 次/轮', '577.110', '812.240', '235.130']], [160, 100, 100, 130])
figure(b, '03_mechanism', '图3 客户端独立随机与共享随机的对照。三个面板依次为未完成率、重试峰值和队列满拒绝次数，均为200轮均值。')
p(b, f'共享减独立的未完成率差为73.94个百分点，95%区间为{ci_pp("mechanism_unfinished")}个百分点；峰值差区间为[88.710, 89.115]次/100毫秒；队列满拒绝差区间为[301.500, 316.450]次/轮。指标同时朝“独立随机更好”的方向变化。')
p(b, '该结果支持本模型中的机制链：独立等待降低到达相关性，减少短时请求集中与队列满拒绝，使工作器更连续地接纳请求，最终提高截止前完成率。共享组虽然每轮也有随机等待，仍明显差于独立组，说明“存在随机数”本身不足以解释收益。')
p(b, '这是对受控模型的机制证据，而非对所有分布式系统的因果定律。全体客户端同步启动、队列仅有5个等待位，都会放大同步到达的代价；真实网络已有的随机延迟或更大的队列可能减弱这种差异。')

b = page('时间线与模型解析')
figure(b, '04_example_timeline', '图4 预先指定种子10000的高负载时间线。上图为累计完成比例，下图为不含首次请求的重试直方图，虚线表示服务恢复。直方图只用于展示，正式峰值采用滑动窗口。')
p(b, '图4使用配置中的首个种子，未按效果挑选“最好看”的案例。独立组在恢复后持续完成任务；无抖动和共享抖动的请求仍表现为成批到达，批次之间工作器可能空闲。所有统计判断仍基于全部200个种子，单条时间线只作解释。')
p(b, '为检查75.72%的高未完成率是否来自实现错误，可对确定性策略作解析复核。在20毫秒失败响应和预设退避下，请求时刻依次为0、0.27、0.79、1.81、3.83、5.85、7.87、9.89、11.91秒。一次同步到达最多接纳“1个处理中＋5个等待”的6个请求。')
p(b, f'若服务在3.83秒时已恢复，前四个可用批次各完成6个，11.91秒批次仅首个于11.96秒完成，总计25个成功；若恢复晚于3.83秒，则少一个批次，共19个成功。日志中恰有{fixed_counts[25]}轮成功25个、{fixed_counts[19]}轮成功19个，故平均成功24.28个、未完成75.72个。这是事后的模型一致性解释，不作为额外独立试验。')

b = page('部分共享实验设计与首次检出档位')
p(b, '基础实验只比较抖动独立和全体共享两个端点。扩展实验[6]固定100台客户端及中等抖动，每个失败序号的等待范围仍为0.5至1.5倍基准等待。只让指定组内的客户端在同一失败序号使用相同抽样，组间和组外各用独立序列。这样可以单独改变多少台客户端一起等待，以及它们组成几个同步组。')
p(b, '单组共享比例从0%到100%，按5个百分点取21档。另设50%共享的分组对照，同一批50台客户端划为五个各10台的组。每种共享结构均运行全体同时启动和1秒内错峰启动两种条件。保持单工作器、5个等待位与12秒全局截止，在200个新种子下配对运行，共8800轮。')
p(b, '各档与同一启动方式的0%共享基线相比。按种子重采样10,000次，用40个档位差的最大绝对偏差构造同时95%区间。区间下界大于0作为检出标准，下界大于5个百分点作为达到本项目实际影响标准的证据。')
threshold_rows = []
for mode, label in [('synchronous', '同时启动'), ('staggered', '1秒内错峰')]:
    thresholds = extension_thresholds[mode]
    detected = thresholds['first_detected_positive_delta']
    practical = thresholds['first_detected_practical_delta']
    picked = [extension_groups[mode, f'shared_{share:03d}'] for share in (detected, practical)]
    threshold_rows.append([label, f'{detected}%', extension_pp(picked[0]['delta_vs_independent']),
                           f'{practical}%', extension_pp(picked[1]['delta_vs_independent'])])
table(b, ['首次请求方式', '首次检出档', '增幅（百分点）', '首次确认超过5点', '增幅（百分点）'],
      threshold_rows, [110, 75, 95, 115, 95])
figure(b, 'sharing_thresholds', '图5 共享比例与未完成率增幅。阴影为同时95%区间，虚线为5个百分点。右图为局部放大。', run=EXTENSION)
p(b, '同时启动时，15%档的区间仍包含0。20%档的增幅区间为[1.68, 4.05]个百分点，25%档为[5.28, 7.65]个百分点。错峰时，90%档的点估计已超过5个百分点，区间仍覆盖该参考值。这些档位的精度受5个百分点采样步长限制。')

b = page('分组和错峰对服务恢复的影响')
p(b, f'扩展实验复核了基础实验的两个端点。同时启动时，完全独立组的未完成率仍为0.00%，完全共享组为{pct(extension_groups["synchronous","shared_100"]["mean_unfinished_rate"])}，与基础实验的{pct(high_shared["mean_unfinished_rate"])}接近。新增对照进一步检验了共享人数相同条件下，组规模与启动时序的影响。')
comparison_rows = []
for mode, profile, label in [
        ('synchronous', 'shared_000', '完全独立，同时启动'),
        ('synchronous', 'shared_050', '1组×50人，同时启动'),
        ('synchronous', 'shared_050_split', '5组×10人，同时启动'),
        ('staggered', 'shared_050', '1组×50人，错峰启动')]:
    row = extension_groups[mode, profile]
    comparison_rows.append([label, pct(row['mean_unfinished_rate']),
                            f'{row["mean_peak_retry_count"]:.2f}', f'{row["mean_queue_rejections"]:.2f}'])
table(b, ['实验条件', '12秒未完成率', '100毫秒重试峰值', '队列满拒绝次数'],
      comparison_rows, [180, 100, 110, 100])
figure(b, 'sharing_mechanisms', '图6 分组和错峰的恢复表现。三项指标分别对应表7的三列，均为200个种子的均值。', run=EXTENSION)
group_effect = extension['contrasts']['sync_one_group_minus_five_groups']
timing_effect = extension['contrasts']['sync_minus_staggered_50_point_effect']
p(b, f'同时启动时，将同一批50台从一个组拆成五个独立组，未完成率下降{extension_pp(group_effect["mean_delta"])}个百分点，配对95%区间为{extension_ci(group_effect["ci95_lower"], group_effect["ci95_upper"])}。按每种启动方式的独立基线计算，错峰使50%共享的额外未完成率下降{extension_pp(timing_effect["mean_delta"])}个百分点，配对95%区间为{extension_ci(timing_effect["ci95_lower"], timing_effect["ci95_upper"])}。')
p(b, '同时启动且15%共享时，重试峰值已从10.94次升至20.70次，但未完成率差异仍未检出。表7和图6的对照表明，拆小同步组与错峰都降低了峰值和拒绝。这与“批量到达挤满队列”的机制一致，也说明只监控截止前完成率可能较晚看到拥堵。')

b = page('探索贡献与协作记录')
lead(b, '本项目完成的探索')
p(b, '第一，依据研究者“优先减少超时客户端”的判断，以共同截止时间下的任务未完成率组织评价，使更少请求不能掩盖更多失败。第二，以均值匹配的分布控制随机幅度，减少平均等待不同导致的混杂。第三，以共享随机等待保持边际分布、改变客户端相关性，为去同步解释提供对照。第四，保存全部随机场景、逐请求与逐客户端日志，并通过解析边界和校验程序构成可追溯证据链。')
p(b, '扩展实验的设计贡献是把共享人数、同组同步规模和启动时序拆为三个可控因素。固定共享人数再改变组规模，是基础实验没有覆盖的对照。该设计能检验哪些同步结构会削弱随机等待的收益，并量化拆组与错峰的效果。指数退避、随机抖动和分散请求是已有方法[2,3]。')
lead(b, '影响外推与公平性的因素')
p(b, '两轮均使用短队列、确定的50毫秒服务时间和固定失败回执。扩展实验增加了1秒内错峰，其他网络延迟、可变服务时间、队列容量与截止时间仍待验证。Google SRE指出拒绝请求也可能消耗资源[4]。检出的共享比例受当前模拟配置、5个百分点的档距和样本量共同约束。')
p(b, '低负载主指标为零形成地板效应，高负载的两种独立抖动也均为零。两轮实验中只有基础实验改变抖动幅度，因此仍缺少用来区分中等和大幅独立抖动的主指标证据。')
lead(b, 'AI协作与研究者参与')
table(b, ['参与方', '实际承担的内容'], [
    ['研究者 周宇科', '选择随机重试主题，提出低负载判断并确定优先减少超时。选择部分共享方向，追问为何共享随机数、差距从哪一档出现，以及新旧实验结论有何不同。'],
    ['AI助手', '核对课件和公开资料，提出均值匹配、分组、错峰与统计方案，编写并运行模拟、校验和绘图代码，依据结果协助整理报告。']], [112, 378])
p(b, '实验数值来自AI协作执行的工程输出。首页可以打开两阶段逐条对话，分别涵盖基础选题与报告，以及扩展实验的设计、运行、结果比较和本次修订。')

b = page('结论与复现资料')
p(b, '对本次命题的回答是：在每轮期望等待匹配、100个客户端同步请求且服务队列较短的模型中，中等独立抖动相较确定性指数退避，显著降低了模拟任务的未按期完成率；低负载条件下没有观察到成功率收益，符合收益主要出现在竞争条件下的预期。共享随机等待未能消除同步到达，因而客户端之间是否独立随机，是本次结果中比“是否有随机数”更关键的设计因素。')
p(b, '“客户端少时固定等待可能足够”仅在本配置得到有限支持；“随机范围越大越好”没有获得主指标上的证据。中等与大幅独立组均为零未完成，不能选出普遍最优幅度。本研究最可靠的结论是一个有条件的机制判断及其复现证据，而非适用于所有真实服务的性能保证。')
p(b, '扩展结果表明，判断共享抖动的风险还需要看每个同步组的规模和启动时序。在本模型中，同步大组表现最差，拆组与错峰都显著降低了50%共享时的额外未完成率。部署中可检查不同客户端的随机流是否独立，并用短窗重试峰值和拒绝数观察是否仍有集中到达。')
lead(b, '运行环境与数据定位')
p(b, '基础实验于2026年11月11日完成，扩展实验于11月19日完成。旧运行记录为1400轮、83000个任务和577798次请求，保留完整日志并已重算指标。新运行保留8800行试验结果、200个种子的场景记录及首个种子的逐请求日志。两轮模拟与统计均使用Python标准库。')
p(b, '在实验目录执行下列命令复现，输出写入新目录。两次运行各自提供实验协议、配置、逐轮结果、统计汇总和文件指纹[5,6]。')
b.append(('code', 'python -m unittest discover -s tests -v\npython run_experiment.py --output results/my_run\npython verify_run.py --run results/my_run\npython run_partial_sharing.py --output results/my_shared_run\npython plot_partial_sharing.py --run results/my_shared_run'))
lead(b, '参考资料')
b.append(('ref', '[1] 课程课件《第2讲 让重复迟到失败都变得无害》 幻灯片9—10。课程提供资料。'))
b.append(('ref', '[2] Brooker M. Exponential Backoff And Jitter. AWS Architecture Blog, 2015-03-04. <link href="https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/" color="#1d4f76">官方原文</link>。检索日期2026-11-12。'))
b.append(('ref', '[3] Amazon Builders’ Library. Timeouts retries and backoff with jitter, 2019-12-05. <link href="https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/" color="#1d4f76">官方原文</link>。检索日期2026-11-12。'))
b.append(('ref', '[4] Forero Cuervo A. Handling Overload. Site Reliability Engineering, Chapter 21. <link href="https://sre.google/sre-book/handling-overload/" color="#1d4f76">官方在线章节</link>。检索日期2026-11-12。'))
b.append(('ref', '[5] 本项目实验工程 results/formal_v1。配置与结果以随报告提供的来源清单和工程manifest为准。原始模拟数据生成日期2026-11-11。'))
b.append(('ref', f'[6] 本项目扩展实验。<link href="{EXTENSION_RESULTS_URL}" color="#1d4f76">配置、结果、图表与日志</link>，2026-11-19。'))

# Portable Markdown is generated from the same content blocks as the PDF.
md = ['# 重试抖动幅度与客户端相关性对故障恢复的影响', '',
      '《海洋数据挖掘技术》课程研究报告', '',
      '**作者：周宇科　学号：22634015　报告更新日期：2026年11月19日**', '',
      f'**公开 GitHub 仓库（Public）：[Yuke-Zhou/-]({REPOSITORY_URL})**', '',
      f'**完整 AI 对话 Markdown：[基础实验与原始报告]({DIALOGUE_URL}) · [部分共享实验与修订]({EXTENSION_DIALOGUE_URL})**', '']
for number, (title, blocks) in enumerate(pages, 1):
    md += [f'## {number} {title}', '']
    for block in blocks:
        kind = block[0]
        if kind == 'cover':
            continue
        if kind in ('p', 'lead', 'ref', 'caption', 'keywords'):
            text = block[1]
            text = re.sub(r'<link href="([^"]+)"[^>]*>(.*?)</link>', r'[\2](\1)', text)
            if kind == 'lead':
                text = '### ' + text
            elif kind == 'p':
                # Full-width spaces survive GitHub's Markdown rendering.
                text = '\u3000\u3000' + text
            md += [text, '']
        elif kind == 'formula':
            text = block[1].replace('<sub>', '_{').replace('</sub>', '}').replace('<super>', '^{').replace('</super>', '}')
            md += ['$$', text, '$$', '']
        elif kind == 'table':
            md.append('| ' + ' | '.join(block[1]) + ' |')
            md.append('| ' + ' | '.join(['---'] * len(block[1])) + ' |')
            md += ['| ' + ' | '.join(row) + ' |' for row in block[2]]
            md.append('')
        elif kind == 'figure':
            md += [f'![{block[2]}](assets/{block[1]})', '', block[2], '']
        elif kind == 'code':
            md += ['```powershell', block[1], '```', '']
(HERE / '课程研究报告_周宇科_22634015.md').write_text('\n'.join(md), encoding='utf-8')

pdfmetrics.registerFont(TTFont('ReportSong', r'C:\Windows\Fonts\simsun.ttc', subfontIndex=0))
pdfmetrics.registerFont(TTFont('ReportHei', r'C:\Windows\Fonts\simhei.ttf'))
pdfmetrics.registerFont(TTFont('ReportMono', r'C:\Windows\Fonts\consola.ttf'))
styles = {
    'body': ParagraphStyle('body', fontName='ReportSong', fontSize=BODY_FONT_SIZE, leading=BODY_LEADING,
                           firstLineIndent=BODY_FIRST_LINE_INDENT,
                           alignment=TA_JUSTIFY, wordWrap='CJK', spaceBefore=0, spaceAfter=6,
                           allowWidows=0, allowOrphans=0),
    'keywords': ParagraphStyle('keywords', fontName='ReportSong', fontSize=BODY_FONT_SIZE,
                               leading=BODY_LEADING, firstLineIndent=0, wordWrap='CJK', spaceAfter=6),
    'lead': ParagraphStyle('lead', fontName='ReportHei', fontSize=11.3, leading=17.5,
                           spaceBefore=8, spaceAfter=5, keepWithNext=True),
    'head': ParagraphStyle('head', fontName='ReportHei', fontSize=15, leading=22,
                           spaceAfter=12, keepWithNext=True),
    'title': ParagraphStyle('title', fontName='ReportHei', fontSize=20, leading=29, alignment=TA_CENTER, spaceAfter=9),
    'subtitle': ParagraphStyle('subtitle', fontName='ReportSong', fontSize=11, leading=17, alignment=TA_CENTER, spaceAfter=6),
    'meta': ParagraphStyle('meta', fontName='ReportSong', fontSize=10.3, leading=18, spaceAfter=8),
    'formula': ParagraphStyle('formula', fontName='ReportSong', fontSize=10.5, leading=18.5, alignment=TA_CENTER, spaceAfter=7),
    'cell': ParagraphStyle('cell', fontName='ReportSong', fontSize=9.4, leading=14.3, wordWrap='CJK'),
    'cell_head': ParagraphStyle('cell_head', fontName='ReportHei', fontSize=9.4, leading=14.5, wordWrap='CJK'),
    'caption': ParagraphStyle('caption', fontName='ReportSong', fontSize=9.0, leading=14,
                              alignment=TA_CENTER, spaceBefore=4, spaceAfter=6, keepWithNext=True),
    'figure_caption': ParagraphStyle('figure_caption', fontName='ReportSong', fontSize=9.0,
                                     leading=14, alignment=TA_CENTER, spaceBefore=5, spaceAfter=8),
    'ref': ParagraphStyle('ref', fontName='ReportSong', fontSize=9.3, leading=15.2,
                          leftIndent=20, firstLineIndent=-20, spaceAfter=6, wordWrap='CJK'),
    'code': ParagraphStyle('code', fontName='ReportMono', fontSize=9, leading=15, spaceAfter=9)
}

story = []
for page_number, (title, blocks) in enumerate(pages, 1):
    if page_number > 1:
        story += [PageBreak(), Paragraph(f'{page_number}　{title}', styles['head'])]
    for block in blocks:
        kind = block[0]
        if kind == 'cover':
            story += [Paragraph('重试抖动幅度与客户端相关性<br/>对故障恢复的影响', styles['title']),
                      Paragraph('海洋数据挖掘技术课程研究报告', styles['subtitle']),
                      Paragraph('作者　周宇科　　学号　22634015　　更新于2026年11月19日', styles['meta']),
                      Paragraph(f'公开 GitHub 仓库（Public）　<link href="{REPOSITORY_URL}" color="#1d4f76">{REPOSITORY_URL}</link><br/>完整 AI 对话 Markdown　<link href="{DIALOGUE_URL}" color="#1d4f76">基础实验与原始报告</link>　·　<link href="{EXTENSION_DIALOGUE_URL}" color="#1d4f76">部分共享实验与修订</link>', styles['meta'])]
        elif kind in ('p', 'lead', 'ref', 'formula', 'caption', 'keywords'):
            story.append(Paragraph(block[1], styles[kind if kind != 'p' else 'body']))
        elif kind == 'table':
            headers, rows, widths = block[1:]
            widths = [width * CONTENT_WIDTH / sum(widths) for width in widths]
            cells = [[Paragraph(html.escape(x), styles['cell_head']) for x in headers]]
            cells += [[Paragraph(html.escape(str(x)), styles['cell']) for x in row] for row in rows]
            t = Table(cells, colWidths=widths, repeatRows=1, hAlign='CENTER')
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0),(-1,0),colors.HexColor('#e8eef2')),
                ('GRID', (0,0),(-1,-1),0.45,colors.HexColor('#d9d9d9')),
                ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
                ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
                ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#fafbfc')])]))
            story += [t, Spacer(1,6)]
        elif kind == 'figure':
            path = ASSETS / block[1]
            iw, ih = ImageReader(str(path)).getSize()
            image_width = min(block[3], CONTENT_WIDTH)
            image = Image(str(path), width=image_width, height=image_width*ih/iw)
            story.append(KeepTogether([image, Paragraph(block[2], styles['figure_caption'])]))
        elif kind == 'code':
            story.append(Paragraph('<br/>'.join(html.escape(line) for line in block[1].splitlines()), styles['code']))


def footer(canvas, document):
    canvas.saveState()
    canvas.setFont('ReportSong', 8.5)
    canvas.setFillColor(colors.HexColor('#555555'))
    canvas.drawString(52, 26, '海洋数据挖掘技术　周宇科　22634015')
    canvas.drawRightString(543, 26, str(document.page))
    canvas.restoreState()


pdf_path = HERE / '课程研究报告_周宇科_22634015.pdf'
pdf_build_path = HERE / '._report_build.pdf'
doc = SimpleDocTemplate(str(pdf_build_path), pagesize=(595.276, 841.89), rightMargin=52.6, leftMargin=52.6,
                        topMargin=43, bottomMargin=42, title='重试抖动幅度与客户端相关性对故障恢复的影响',
                        author='周宇科', subject='海洋数据挖掘技术课程研究报告')
doc.build(story, onFirstPage=footer, onLaterPages=footer)
try:
    pdf_build_path.replace(pdf_path)
except PermissionError:
    pdf_path = HERE / f'课程研究报告_周宇科_22634015_{REPORT_DATE}.pdf'
    pdf_build_path.replace(pdf_path)
sources = {
    'author': '周宇科', 'student_id': '22634015', 'report_date': REPORT_DATE,
    'github_url': REPOSITORY_URL, 'dialogue_url': DIALOGUE_URL,
    'extension_dialogue_url': EXTENSION_DIALOGUE_URL,
    'format': {'paper': 'A4', 'body_font': 'SimSun', 'body_font_size_pt': BODY_FONT_SIZE,
               'body_leading_pt': BODY_LEADING, 'first_line_indent_characters': 2,
               'first_line_indent_pt': BODY_FIRST_LINE_INDENT, 'body_space_after_pt': 6,
               'table_captions': 'above', 'figure_captions': 'below',
               'references': 'hanging indent', 'markdown_indent': 'two full-width spaces'},
    'formal_run': 'experiments/retry_jitter/results/formal_v1',
    'inputs_sha256': {name: digest(RUN/name) for name in ['analysis.json','manifest.json','effective_config.json','trials.csv']},
    'analytical_check': {'no_jitter_success_counts': dict(fixed_counts), 'interpretation': 'post-hoc model consistency explanation'},
    'raw_counts': {'trials':len(trials),'clients':client_count,'attempts':attempt_count},
    'extension_run': 'experiments/retry_jitter/results/partial_sharing_v1',
    'extension_inputs_sha256': {name: digest(EXTENSION/name) for name in
                                ['analysis.json', 'manifest.json', 'effective_config.json', 'trials.csv',
                                 'figures/sharing_thresholds.png', 'figures/sharing_mechanisms.png']},
    'extension_counts': {'trials': extension['trial_count'], 'seeds': extension['seed_count']},
    'references_access_date': '2026-11-12',
    'limitations': ['synthetic finite queue','zero observed failures is not zero population risk','no algorithm novelty established'],
    'outputs_sha256': {p.name:digest(p) for p in [pdf_path,HERE/'课程研究报告_周宇科_22634015.md']}
}
(HERE/'report_sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'pdf':str(pdf_path),'markdown':str(HERE/'课程研究报告_周宇科_22634015.md'),
                  'planned_pages':len(pages),'source_audit':'passed'},ensure_ascii=False,indent=2))
