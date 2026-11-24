import {feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {BACKTEST} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

function percent(rate) {
  return rate === null || rate === undefined ? '—' : `${(rate * 100).toFixed(1)}%`;
}

function people(count, digits = 1) {
  return count === null || count === undefined ? '—' : `${count.toFixed(digits)} 人`;
}

function signed(count) {
  if (count === null || count === undefined) return '—';
  return `${count > 0 ? '+' : ''}${count.toFixed(1)}`;
}

function predictionTable(cells) {
  return table([
    {key:'channel', label:'招生渠道'}, {key:'program', label:'培训项目'},
    {key:'cohort', label:'当月圈定'},
    {label:'历史确认 / 受理', render:row => row.sample_intakes
      ? `${row.sample_enrolled} / ${row.sample_intakes}` : '—'},
    {label:'历史转化率', render:row => percent(row.rate)},
    {label:'预估确认', render:row => row.estimated_enrolled === null
      ? (row.flag || '—') : people(row.estimated_enrolled)},
  ], cells, {caption:'渠道 × 项目预估'});
}

function actualTable(cells) {
  return table([
    {key:'channel', label:'招生渠道'}, {key:'program', label:'培训项目'},
    {key:'cohort', label:'圈定人数'},
    {label:'预估确认', render:row => row.predicted_enrolled === null
      ? (row.flag || '—') : people(row.predicted_enrolled)},
    {label:'实绩确认', render:row => row.actual_enrolled ?? '—'},
    {label:'误差', render:row => signed(row.error)},
  ], cells, {caption:'渠道 × 项目实绩'});
}

function predictionPanel(job) {
  const prediction = job.prediction;
  const known = !prediction.missing_batch;
  const summary = [
    {label:'统计月份', value:prediction.period},
    {label:'圈定学员', value:known ? `${prediction.cohort_size} 人` : '—'},
    {label:'预估确认报名', value:known ? people(prediction.estimated_enrolled) : '—',
     detail:known ? `已覆盖 ${prediction.covered_cohort} 人` : '缺批，未能估算'},
    {label:'估算样本月', value:prediction.sample_months.length ? prediction.sample_months.join('、') : '无',
     detail:'早于目标月且已满 30 天'},
  ];
  const body = [
    metrics(summary),
    prediction.missing_batch
      ? p(`目标月末前没有含 ${prediction.period} 受理记录的批次入库，未能圈定学员。报表日之后补传的批次不参与本次预测；如需纳入，请重新发起回测。`, 'empty')
      : null,
    prediction.missing_sample_cohort
      ? p(`${prediction.missing_sample_cohort} 名圈定学员所在单元格缺估算样本，未计入预估合计。`, 'subtle') : null,
    prediction.cohort_size ? predictionTable(prediction.cells) : null,
    flagList(prediction.flags),
  ];
  return panel(`预测 · ${job.dataset_name || (job.dataset_id ? `批次 ${job.dataset_id}` : '缺批')}`, ...body);
}

function actualPanel(job) {
  if (!job.actual) {
    const text = job.settlement_ready
      ? '已满 30 天，后台将用当时最后一批核对圈定学员的实绩。'
      : `目标月末满 30 天后（${job.maturity_date}）才会核对实绩，预测原样保留。`;
    return panel('实绩核对', p(text, 'subtle'));
  }
  const actual = job.actual;
  const summary = [
    {label:'预估确认', value:people(actual.predicted_enrolled)},
    {label:'实绩确认', value:actual.actual_enrolled === null ? '—' : `${actual.actual_enrolled} 人`},
    {label:'误差（实绩 − 预估）', value:signed(actual.error)},
    {label:'误差率', value:percent(actual.error_rate), detail:'分母为实绩确认人数'},
    {label:'实绩批次', value:job.actual_dataset_name || (job.actual_dataset_id ? `批次 ${job.actual_dataset_id}` : '缺批'),
     detail:`核对 ${actual.matched_learners} 人`},
  ];
  return panel('实绩核对', metrics(summary),
    actual.cells.length ? actualTable(actual.cells) : p('缺批，未核对实绩。', 'empty'),
    flagList(actual.flags));
}

function flagList(flags) {
  if (!flags?.length) return null;
  return el('div', {class:'empty', role:'status'}, h(3, '需要留意'), el('ul', {class:'method-list'}, flags.map(text => el('li', {}, text))));
}

function detailView(job) {
  return panel(job.name,
    p(`${job.period} · 发起人 ${job.author} · ${date(job.created_at)}`, 'eyeline'),
    el('div', {class:'actions'}, pill(BACKTEST[job.status], job.status),
      job.status === 'failed' && p(job.error, 'feedback')));
}

function panels(job) {
  return [detailView(job), predictionPanel(job), actualPanel(job)];
}

export async function backtestPage(route, refresh) {
  const {spec} = workspace;
  if (!session.canWrite) {
    return panel(spec.pages[6], empty('回测仅开放给分析员与复核主管。内部预测与实绩不对外发布。', '#results', '查看公开结果'));
  }
  const items = await workspace.backtests();
  const id = route.id || items[0]?.id;
  const job = id ? await workspace.getBacktest(id) : null;
  const message = feedback();
  const original = location.hash;
  let pending = null;

  if (job && (job.status === 'queued' || job.status === 'running')) {
    const status = el('p', {role:'status'}, '后台正在计算，完成后会自动展示。');
    const active = () => location.hash === original;
    poll(()=>workspace.getBacktest(id), item=>item.status !== 'queued' && item.status !== 'running', active)
      .then(result=>{if (result) refresh(); else if (status.isConnected) status.textContent = '仍在执行，请稍后刷新。';})
      .catch(error=>{if (status.isConnected) status.textContent = error.message;});
    pending = status;
  }

  const submit = button('发起回测', {type:'submit', class:'primary', 'data-testid':'start-backtest'});
  const form = el('form', {class:'fields', onSubmit:handler(submit, message, async event=>{
    const data = new FormData(event.target);
    const item = await workspace.startBacktest({name:data.get('name'), period:data.get('period')});
    location.hash = `#backtest/${item.id}`;
    await refresh();
  })}, field({key:'name', label:'回测名称', type:'string', max_length:120}, `样本 ${spec.pages[6]}`),
    field({key:'period', label:'目标月份', type:'string', max_length:7, help:'填写 YYYY-MM，按北京时间自然月圈定学员。'}, '2026-11'),
    el('div', {class:'actions'}, submit), message);

  const list = table([
    {label:'回测', render:row=>link(row.name, `#backtest/${row.id}`)},
    {label:'目标月份', render:row=>row.period},
    {label:'状态', render:row=>pill(BACKTEST[row.status], row.status)},
    {label:'预估确认', render:row=>row.estimated_enrolled === null || row.estimated_enrolled === undefined
      ? '—' : `${row.estimated_enrolled.toFixed(1)} 人`},
    {label:'误差', render:row=>row.error_value === null || row.error_value === undefined
      ? '—' : `${row.error_value > 0 ? '+' : ''}${row.error_value.toFixed(1)}`},
    {label:'发起时间', render:row=>date(row.created_at)},
  ], items, {caption:'回测记录', empty:'还没有回测记录。'});

  const canSettle = job && job.status === 'predicted' && job.settlement_ready && !job.actual;
  let settle = null;
  if (canSettle) {
    const settleButton = button('立即核对实绩', {class:'quiet'});
    settleButton.addEventListener('click', handler(settleButton, message, async()=>{
      await workspace.settleBacktest(job.id); await refresh();
    }));
    settle = panel('实绩核对', p('已满 30 天，可手动触发核对；也可等待后台自动补齐。','subtle'), settleButton);
  }

  const intro = p('用月末前最后入库的含当月数据批次圈定学员，按此前已满 30 天各月同渠道同项目的合并转化率估算月末确认报名；满 30 天后再用当时最后一批只核对已圈定学员的实绩。', 'subtle');
  const current = !job ? empty('选择或发起一次回测。')
    : job.prediction
      // 结算中仍展示已出的预测，附带后台状态，避免看起来像重新计算。
      ? el('div', {class:'wide-panel'}, panels(job), pending ? panel('后台状态', pending) : null)
      : panel(job.name, pill(BACKTEST[job.status], job.status), pending || p('等待后台生成预测。', 'subtle'));
  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'}, intro, current, settle),
    el('aside', {class:'sidebar-panel'},
      panel('发起回测', form),
      panel('回测记录', list),
      panel('口径说明', el('ul', {class:'method-list'}, [
        '预测批次在发起时锁定，之后补传的新批次不会改动已出的预测。',
        '预估按渠道 × 项目合并历史成熟月份的确认与受理人数后求转化率，再加权到当月圈定人数。',
        '缺批或缺估算样本会单独标出，不按 0 计入。',
        '实绩只看已圈定学员在目标月的确认，新学员和归属变化不改变分母。',
      ].map(text=>el('li', {}, text))))));
}
