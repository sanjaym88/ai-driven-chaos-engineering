const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (v, unit = '') => typeof v === 'number' && Number.isFinite(v) ? `${v.toLocaleString(undefined, {maximumFractionDigits: 1})}${unit}` : '—';
const title = v => String(v ?? 'unknown').replaceAll('_', ' ');
let current = null, busy = false;
async function get(url) { const r = await fetch(url); if (!r.ok) throw new Error(`Could not load artifacts (${r.status})`); return r.json(); }
function pairs(id, values) { $(id).innerHTML = values.map(([k,v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v ?? 'Unknown')}</dd></div>`).join(''); }
function chart(rows) {
  const valid = rows.filter(r => Number.isFinite(r.latency?.p95));
  if (!valid.length) { $('chart').innerHTML = '<p class="muted">No latency observations for this run.</p>'; return; }
  const width = 1000, height = 225, left = 55, right = 980, top = 15, bottom = 188;
  const times = valid.map(r => typeof r.timestamp === 'number' ? r.timestamp * 1000 : Date.parse(r.timestamp));
  const usableTime = times.every(Number.isFinite), start = Math.min(...times), end = Math.max(...times);
  const max = Math.max(1, ...valid.map(r => r.latency.p95)) * 1.15;
  const x = i => left + (usableTime && end > start ? (times[i] - start) / (end - start) : i / Math.max(valid.length - 1, 1)) * (right - left);
  const y = v => bottom - v / max * (bottom - top);
  let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Window p95 latency in milliseconds across baseline, fault and recovery">`;
  for(let i = 0; i < 4; i++) { const v = max * i / 3; svg += `<line x1="${left}" y1="${y(v)}" x2="${right}" y2="${y(v)}" stroke="#e5e5e5"/><text x="44" y="${y(v)+4}" text-anchor="end" font-size="10" fill="#777">${Math.round(v)}</text>`; }
  // Separate phase paths so no interpolated line implies unobserved measurements.
  for(const phase of ['baseline','fault','recovery']) {
    const indexes = valid.map((r,i) => r.phase === phase ? i : -1).filter(i => i >= 0);
    if(!indexes.length) continue;
    const points = indexes.map(i => `${x(i)},${y(valid[i].latency.p95)}`).join(' ');
    svg += `<polyline points="${points}" fill="none" stroke="${phase==='baseline'?'#999':'#111'}" stroke-width="${phase==='fault'?2.5:1.5}" ${phase==='recovery'?'stroke-dasharray="5 4"':''}/>`;
    for(const i of indexes) svg += `<circle cx="${x(i)}" cy="${y(valid[i].latency.p95)}" r="3" fill="white" stroke="#222"><title>${esc(phase)} · ${number(valid[i].latency.p95,' ms')}</title></circle>`;
    svg += `<text x="${(x(indexes[0])+x(indexes.at(-1)))/2}" y="215" text-anchor="middle" font-size="10" fill="#777">${phase.toUpperCase()}</text>`;
  }
  $('chart').innerHTML = svg + '</svg>';
}
function render(data) {
  current = data;
  const p3 = data.p3 || {}, p4 = data.p4 || {}, plan = data.plan || {}, s = data.summary;
  $('content').hidden = false; $('empty').hidden = true; $('download').disabled = false;
  $('execution').textContent = title(p3.status || data.state?.state);
  $('reason').textContent = title(p3.stop_reason || 'No final result yet');
  $('p95').textContent = number(s.fault.p95, ' ms');
  $('comparison').textContent = s.baseline.p95 > 0 && s.fault.p95 != null ? `${number(s.fault.p95 / s.baseline.p95)}× baseline p95` : 'No baseline comparison';
  $('cleanup').textContent = p3.cleanup_verified === true ? 'Verified' : p3.cleanup_verified === false ? 'Not verified' : 'Unknown';
  $('recovery').textContent = title(p4.recovery_result || 'unknown');
  $('validity').textContent = p4.status ? `P4 evidence: ${p4.status}` : 'Awaiting P4 evaluation';
  $('notice').textContent = data.warnings.join(' · ');
  $('samples').textContent = `${number(data.metrics.reduce((n,r)=>n+r.request_count,0))} requests · ${data.metrics.length} windows`;
  chart(data.metrics);
  const edge = data.service_map?.dependencies?.find(e => e.to === plan.target);
  $('map').innerHTML = edge ? `<div class="nodes"><div class="node">${esc(edge.from)}<small>Caller</small></div><span class="arrow">→</span><div class="node proxy">${esc(edge.via)}<small>Fault boundary</small></div><span class="arrow">→</span><div class="node">${esc(edge.to)}<small>Dependency</small></div></div>` : '<p class="muted">No mapped dependency for this target.</p>';
  $('route').textContent = edge ? `${edge.path} · Map verification: ${edge.verification || 'unknown'} (recorded configuration)` : '';
  pairs('plan', [['Fault', title(plan.fault_type)],['Target',plan.target],['Intensity',plan.intensity],['Maximum duration',number(plan.max_duration,' s')],['Latency stop',number(plan.stop_thresholds?.max_latency_p95_ms ?? plan.stop_thresholds?.p95_latency_ms,' ms')],['Error stop',number((plan.stop_thresholds?.max_error_rate ?? plan.stop_thresholds?.error_rate) * 100,'%')]]);
  $('phases').innerHTML = Object.entries(s).map(([phase,r])=>`<tr><td>${phase}</td><td>${number(r.requests)}</td><td>${number(r.errors)}</td><td>${r.error_rate == null ? '—' : number(r.error_rate*100,'%')}</td><td>${number(r.p50,' ms')}</td><td>${number(r.p95,' ms')}</td><td>${number(r.p99,' ms')}</td></tr>`).join('');
  const flags = p4.failure_flags || [];
  $('findings').innerHTML = flags.length ? flags.map(f=>`<div class="finding">↳ ${esc(title(f))}</div>`).join('') : `<p class="muted">${p4.status === 'valid' ? 'No failure flags were recorded. Review the phase measurements and recovery evidence.' : 'No accepted evaluation yet. Findings are unknown.'}</p>`;
  $('files').innerHTML = data.evidence.map(f=>`<span>${esc(f)}</span>`).join('');
  const time = value => Number.isFinite(value) ? new Date(value*1000).toLocaleTimeString() : 'Not recorded';
  const t = p3.timeline || {};
  pairs('timeline',[['Armed',time(t.armed_at)],['Injected',time(t.injected_at)],['Removed',time(t.removed_at)],['Finished',time(t.finished_at)],['Fault duration',number(t.fault_duration_s,' s')]]);
  $('raw').textContent = JSON.stringify({run_id:data.run_id,p3:data.p3,p4:data.p4},null,2);
}
async function refresh() {
  if(busy) return; busy = true; $('refresh').disabled = true;
  try {
    const list = await get('/api/runs'), selected = $('run').value;
    $('run').innerHTML = list.runs.map(r=>`<option value="${esc(r.run_id)}">${esc(r.run_id)} / ${esc(title(r.fault_type))}</option>`).join('');
    if(list.runs.some(r=>r.run_id===selected)) $('run').value=selected;
    if(!list.runs.length) { $('run').innerHTML='<option>No runs available</option>'; $('content').hidden=true; $('empty').hidden=false; $('download').disabled=true; current=null; $('notice').textContent=list.warnings.join(' · '); }
    else render(await get(`/api/run?id=${encodeURIComponent($('run').value)}`));
    $('connection').textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch(e) { $('connection').textContent='Disconnected'; $('notice').textContent=`${e.message}. Displayed evidence may be stale.`; }
  finally { busy=false; $('refresh').disabled=false; }
}
$('refresh').addEventListener('click',refresh);
$('run').addEventListener('change',refresh);
$('download').addEventListener('click',()=>{ if(!current)return; const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'})); const a=document.createElement('a');a.href=url;a.download=`${current.run_id}-report.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); });
setInterval(()=>{if($('auto').checked)refresh();},5000);
refresh();
