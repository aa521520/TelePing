import {channelText,operatorName} from './report.mjs';

const MAX_COMPLETED_ROUNDS = 30;
export function unitPrice(env) {
  const raw=env.REFERENCE_NODE_PRICE_MICRO;
  if(raw===undefined||raw==='') return 10000;
  if(!/^\d{1,12}$/.test(String(raw))) throw Error('invalid_reference_price');
  return Number(raw);
}
export function createRound(s, jobs, {source='legacy', price=10000, incomplete=false}={}) {
  if(!jobs.length)return;
  s.rounds ||= {};
  const first=jobs[0];
  if(Object.hasOwn(s.rounds,first.round_id))return;
  s.rounds[first.round_id]={id:first.round_id,created:Date.now()/1000,source,total:jobs.length,profile:structuredClone(first.profile),price,incomplete,items:Object.fromEntries(jobs.map(j=>[j.id,{status:'pending'}])),partial_sent:false,reported:false};
}
export function migrateRounds(s,env) {
  s.rounds ||= {};
  const groups=new Map();
  for(const job of s.jobs) {
    if(Object.hasOwn(s.rounds,job.round_id))continue;
    if(!groups.has(job.round_id))groups.set(job.round_id,[]);
    groups.get(job.round_id).push(job);
  }
  for(const jobs of groups.values()){createRound(s,jobs,{price:unitPrice(env),incomplete:true});for(const job of jobs){if(job.status==='cancelled')recordRound(s,job,{status:'cancelled',submitted:false,node_count:0});else if(job.status==='terminal'||job.status==='uncertain'){const prior=s.history.findLast?.(x=>x.id===job.id);recordRound(s,job,prior?{...prior,submitted:null}:{status:'uncertain',submitted:null,node_count:null})}}}
  for(const item of s.outbox||[])if(typeof item.text==='string')item.text=item.text.replace(/\n\n累计费用：暂不可查询｜余额：(未配置|暂不可查询)$/,'');
}
function count(value){return Number.isSafeInteger(value)&&value>=0&&value<=10000?value:null}
export function recordRound(s,job,result) {
  const round=s.rounds?.[job.round_id], old=round?.items?.[job.id];
  if(!old||old.status!=='pending')return false;
  let allocated=Object.hasOwn(result,'allocated_node_count')?count(result.allocated_node_count):count(job.allocated_node_count);
  let submitted=typeof result.submitted==='boolean'?result.submitted:(typeof job.submitted==='boolean'?job.submitted:(job.status==='claimed'||job.status==='queued'?false:null));
  const nodeCount=count(result.node_count);
  if(submitted===false&&((nodeCount||0)>0||(allocated||0)>0)){submitted=null;allocated=null}
  if(allocated!==null&&nodeCount!==null&&nodeCount>allocated)allocated=null;
  let category=result.status;
  if(category==='complete')category=allocated!==null&&nodeCount!==null&&allocated>nodeCount?'incomplete':result.failure_count>0?'node_failed':'normal';
  if(!['normal','node_failed','incomplete','failed','uncertain','blocked','cancelled'].includes(category))category='uncertain';
  const anomaly=category!=='normal';
  round.items[job.id]={status:category,allocated,submitted,node_count:nodeCount,operators:result.operators||{},...(anomaly?{target:job.cancelled?'已删除':job.target,task_id:job.task_id||null,error_code:result.error_code||null,success_count:count(result.success_count),failure_count:count(result.failure_count)}:{})};
  return true;
}
function cents(micro){let n=BigInt(micro),whole=n/1000000n,decimal=String(n%1000000n).padStart(6,'0').replace(/0+$/,'');return decimal?`${whole}.${decimal}`:String(whole)}
function stats(round){let totals={normal:0,node_failed:0,failed:0,uncertain:0,blocked:0,cancelled:0,pending:0,incomplete:0,priced:0,unconfirmed:0,fallback:0};
  for(const item of Object.values(round.items)) {
    totals[item.status]++;
    if(item.status==='pending')continue;
    if(item.submitted===false)continue;
    if(item.allocated!==null){totals.priced+=item.allocated;continue}
    if(item.node_count!==null&&item.node_count>0){totals.priced+=item.node_count;totals.fallback++}
    totals.unconfirmed++;
  }
  return totals;
}
function carrierSummary(round) {
  const carriers={};
  for(const item of Object.values(round.items)) {
    for(const [id,v] of Object.entries(item.operators||{})) {
      const o=carriers[id]||={node_count:0,success_count:0,failure_count:0,entries:0,onlyAverage:null};
      o.node_count+=v.node_count;
      o.success_count+=v.success_count;
      o.failure_count+=v.failure_count;
      o.entries++;
      o.onlyAverage=v.avg_seconds;
    }
  }
  return Object.entries(carriers).map(([id,v])=>{
    const average=v.entries===1&&v.onlyAverage!==null?`，平均 ${v.onlyAverage} 秒`:'';
    return `${operatorName(id)}：节点 ${v.node_count}，成功 ${v.success_count}，失败 ${v.failure_count}${average}`;
  }).join('｜');
}

function fee(round,t){const unit=cents(round.price);let amount=cents(BigInt(round.price)*BigInt(t.priced));let basis=`参考价 ${unit} 元/节点，已知 ${t.priced} 个节点`;if(t.unconfirmed)basis+=`，${t.unconfirmed} 项费用待核实`;if(t.fallback)basis+=`（${t.fallback} 项按已收到节点不完整估算）`;let estimate=t.priced||!t.unconfirmed?`${amount} 元`:'待核实';return `轮次：${round.id}｜本轮费用参考估算：${estimate}（${basis}；各页为同一总额，不相加，实际以服务商结算为准）`}
function page(body,footer){const max=3500-footer.length-2,out=[];while(body.length>max){let cut=body.lastIndexOf('\n',max);if(cut<Math.floor(max/2))cut=max;if(/[\uD800-\uDBFF]/.test(body[cut-1]))cut--;out.push(body.slice(0,cut)+'\n\n'+footer);body=body.slice(cut).replace(/^\n/,'')}if(body)out.push(body+'\n\n'+footer);return out}
export function makeReports(round,partial=false){const t=stats(round),label=partial?'检测部分汇总（已暂停，待测任务保留）':'检测本轮汇总';let summary=`${label}\n目标：${round.total}｜正常：${t.normal}｜节点异常：${t.node_failed}｜结果不完整：${t.incomplete}｜检测失败：${t.failed+t.blocked}｜待确认：${t.uncertain}｜取消：${t.cancelled}｜待测：${t.pending}\n${channelText(round.profile)}${carrierSummary(round)?'\n'+carrierSummary(round):''}${round.incomplete?'\n历史轮次数据不完整，数量仅反映仍可核对的任务。':''}`;let footer=fee(round,t),out=page(summary,footer);if(partial)return out;let details=[];for(let item of Object.values(round.items)){if(item.status==='pending'||item.status==='normal')continue;let state=({node_failed:'部分节点异常',incomplete:'节点结果不完整',failed:'检测失败',uncertain:'提交或结果待确认',blocked:'检测未启动',cancelled:'已取消'})[item.status];let line=`${state}：${item.target}`;if(item.error_code==='provider_result_incomplete')line+='（结果不完整，待核对）';if(item.error_code==='no_results')line+='（未收到节点结果）';if(item.error_code==='provider_auth_failed')line+='（账号认证未通过）';if(item.node_count!==null)line+=`｜收到节点 ${item.node_count}`;if(item.failure_count)line+=`｜失败节点 ${item.failure_count}`;let carriers=Object.entries(item.operators||{}).map(([id,v])=>`${operatorName(id)} ${v.node_count} 节点/${v.failure_count} 失败`);if(carriers.length)line+='｜'+carriers.join('、');if(item.task_id&&(item.status==='uncertain'||item.status==='failed'))line+=`｜任务号 ${item.task_id}`;details.push(line)}if(details.length)out.push(...page('异常明细\n'+details.join('\n\n'),footer));return out}
export function publishRound(s,roundId,enqueue,{partial=false}={}){let round=s.rounds?.[roundId];if(!round||round.reported||(partial&&round.partial_sent))return;let pending=Object.values(round.items).some(x=>x.status==='pending');if(partial&&!pending)return;if(!partial&&pending)return;for(let text of makeReports(round,partial))enqueue(s,text);if(partial)round.partial_sent=true;else{round.members=Object.keys(round.items);round.totals=stats(round);round.estimate=fee(round,round.totals);round.items={};round.reported=true}}
export function pruneRounds(s){let ids=Object.keys(s.rounds||{}).filter(id=>s.rounds[id].reported&&!s.jobs.some(j=>j.round_id===id));while(ids.length>MAX_COMPLETED_ROUNDS)delete s.rounds[ids.shift()]}
