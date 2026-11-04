import {recordRound,publishRound,pruneRounds} from './rounds.mjs';
import {encrypt,decrypt,chunks} from '../cloudflare/core.mjs';
export const id=()=>crypto.randomUUID();
export const seconds=()=>Date.now()/1000;
export const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
export const validRun=x=>typeof x==='string'&&/^[1-9][0-9]{0,29}-[1-9][0-9]{0,9}$/.test(x);
export function order(a,b){let [x,y]=a.split('-').map(BigInt),[z,w]=b.split('-').map(BigInt);return x<z?-1:x>z?1:y<w?-1:y>w?1:0}
export function initial(env){return {chat_id:env.TELEGRAM_CHAT_ID,targets:[],generations:{},profile:{test_type:'HTTP',operators:[1,2,7],area:1,provinces:[],cities:[],node_type:1,nodes_per_region:1},interval_minutes:1440,budget:null,daily_enabled:false,daily_preference:true,last_daily:0,credentials:null,config_version:0,jobs:[],history:[],sessions:{},private_sessions:{},seen:[],inbox:[],outbox:[],receipts:{},lease:null,next_send:0,dispatch:null}}
export function ready(s){return !!(s.credentials?.username&&s.credentials?.api_token&&!s.auth_blocked)}
export function queue(s,text,{chat=s.chat_id,markup,priority=false,session,nonce,parse_mode}={}) {
  const entries=chunks(text).map(text=>({text,chat,markup,session,nonce,parse_mode}));
  if(priority)s.outbox.unshift(...entries);else s.outbox.push(...entries);
}
export function cancelUnstarted(s,job) {
  if(job.status==='begun'){job.cancelled=true;return}
  if(job.status==='terminal'||job.status==='uncertain'||job.status==='cancelled')return;
  recordRound(s,job,{status:'cancelled',submitted:false,node_count:0});
  job.status='cancelled';
  publishRound(s,job.round_id,queue);
}
export function finishLease(s) {
  if(!s.lease)return;
  const touched=new Set();
  for(const job of s.jobs) {
    if(job.lease!==s.lease.run_id)continue;
    touched.add(job.round_id);
    if(job.status==='claimed'){job.status='queued';delete job.lease}
    else if(job.status==='begun') {
      recordRound(s,job,{status:'uncertain',node_count:null,allocated_node_count:job.allocated_node_count,submitted:job.submitted});
      job.status='uncertain';
      s.history.push({id:job.id,status:'uncertain',task_id:job.task_id||null});
    }
  }
  s.jobs=s.jobs.filter(j=>!['uncertain','terminal','cancelled'].includes(j.status));
  s.history=s.history.slice(-200);
  s.receipts[s.lease.run_id]={executor_id:s.lease.executor_id,lease_token:s.lease.lease_token};
  while(Object.keys(s.receipts).length>64)delete s.receipts[Object.keys(s.receipts)[0]];
  s.lease=null;
  for(const roundId of touched)publishRound(s,roundId,queue);
  pruneRounds(s);
}
export function jobValid(s,j){return !j.cancelled&&(!j.managed||s.generations[j.target]===j.generation)}
export async function runner(service,path,b,s){let run=b.run_id;if(!validRun(run))return json({ok:false},400);let lease=s.lease;
 if(path==='/api/claim'){
  if(typeof b.executor_id!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(b.executor_id))return json({ok:false},400);
  if(Object.hasOwn(s.receipts,run))return json({should_check:false});
  if(lease&&lease.expires>seconds())return lease.run_id===run&&lease.executor_id===b.executor_id?json({should_check:true,payload:lease.payload}):json({should_check:false});
  if(lease)finishLease(s);
  if(s.highwater&&order(run,s.highwater)<=0){await service.save(s);return json({ok:false},409)}
  if(!ready(s)){await service.save(s);return json({should_check:false})}
  let jobs=s.jobs.filter(j=>j.status==='queued'&&jobValid(s,j)).slice(0,20);if(!jobs.length){await service.save(s);return json({should_check:false})}
  let lease_token=id(),expires=seconds()+7800;
  for(let job of jobs){job.status='claimed';job.lease=run;job.config_version=s.config_version}
  let plan={run_id:run,executor_id:b.executor_id,lease_token,expires,config_version:s.config_version,credentials:s.credentials,jobs:jobs.map(({id,target,generation,round_id,profile})=>({id,target,generation,round_id,profile}))};
  let payload=await encrypt(plan,service.env.STATE_ENCRYPTION_KEY,'17ce:plan:'+run);s.highwater=run;s.lease={run_id:run,executor_id:b.executor_id,lease_token,expires,payload};await service.save(s);return json({should_check:true,payload});
 }
 if(path==='/api/finish'&&Object.hasOwn(s.receipts,run)){let r=s.receipts[run];return r.executor_id===b.executor_id&&r.lease_token===b.lease_token?json({ok:true,duplicate:true}):json({ok:false},409)}
 if(!lease||lease.run_id!==run||lease.expires<=seconds())return path==='/api/begin'?json({permit:false}):json({ok:false},409);
 if(path==='/api/event'){
  let d;try{d=await decrypt(b.payload,service.env.STATE_ENCRYPTION_KEY,'17ce:event:'+run)}catch{return json({ok:false},400)}
  if(d.run_id!==run||d.executor_id!==lease.executor_id||d.lease_token!==lease.lease_token||!['accepted','terminal'].includes(d.kind)||d.event_id!==`${d.job_id}:${d.kind}`)return json({ok:false},409);
  let job=s.jobs.find(j=>j.id===d.job_id&&j.lease===run);if(!job)return json({ok:false},409);
  if(job.events?.includes(d.kind)||(job.status==='terminal'&&d.kind==='accepted'))return json({ok:true,duplicate:true});if(job.status!=='begun'&&!(d.kind==='terminal'&&job.status==='claimed'&&['provider_auth_failed','provider_connect_failed','begin_unavailable','configuration_invalid'].includes(d.result?.error_code)))return json({ok:false},409);
  let r=d.result;if(!r||typeof r!=='object')return json({ok:false},400);if(r.submitted!==undefined&&typeof r.submitted!=='boolean')return json({ok:false},400);if(r.allocated_node_count!==undefined&&r.allocated_node_count!==null&&(!Number.isSafeInteger(r.allocated_node_count)||r.allocated_node_count<0||r.allocated_node_count>10000))return json({ok:false},400);
  if(d.kind==='accepted'){if(typeof r.task_id!=='string'||r.task_id.length>200||/[\x00-\x1f\x7f]/.test(r.task_id))return json({ok:false},400);job.task_id=r.task_id;if(r.allocated_node_count!==undefined)job.allocated_node_count=r.allocated_node_count;if(r.submitted!==undefined)job.submitted=r.submitted}
  else {if(r.task_id!==undefined&&(typeof r.task_id!=='string'||r.task_id.length>200||/[\x00-\x1f\x7f]/.test(r.task_id)))return json({ok:false},400);if(r.task_id)job.task_id=r.task_id;if(!['complete','failed','uncertain','blocked'].includes(r.status))return json({ok:false},400);for(let k of ['node_count','success_count','failure_count'])if(!Number.isSafeInteger(r[k])||r[k]<0||r[k]>10000)return json({ok:false},400);if(r.error_code&&!['provider_auth_failed','provider_connect_failed','begin_unavailable','provider_disconnected','provider_timeout','provider_task_failed','provider_result_incomplete','no_results','configuration_invalid'].includes(r.error_code))return json({ok:false},400);if(!r.operators||typeof r.operators!=='object'||Array.isArray(r.operators)||Object.keys(r.operators).length>100)return json({ok:false},400);for(let [key,v] of Object.entries(r.operators)){if(!/^(?:[0-9]{1,6}|unknown)$/.test(key)||!v||typeof v!=='object')return json({ok:false},400);for(let k of ['node_count','success_count','failure_count'])if(!Number.isSafeInteger(v[k])||v[k]<0||v[k]>10000)return json({ok:false},400);for(let k of ['avg_seconds','max_seconds'])if(v[k]!==null&&(typeof v[k]!=='number'||!Number.isFinite(v[k])||v[k]<0))return json({ok:false},400)}r.operators=Object.fromEntries(Object.entries(r.operators).map(([k,v])=>[k,{node_count:v.node_count,success_count:v.success_count,failure_count:v.failure_count,avg_seconds:v.avg_seconds,max_seconds:v.max_seconds}]));if(r.error_code==='provider_auth_failed'&&job.config_version===s.config_version){s.auth_blocked=true;s.daily_enabled=false;s.dispatch=null}recordRound(s,job,r);
    job.status='terminal';
    s.history.push({id:job.id,status:r.status,task_id:job.task_id||null,node_count:r.node_count,success_count:r.success_count,failure_count:r.failure_count,operators:r.operators,...(r.error_code?{error_code:r.error_code}:{})});
    s.history=s.history.slice(-200);
    if(r.error_code==='provider_auth_failed'&&job.config_version===s.config_version)publishRound(s,job.round_id,queue,{partial:true});
    publishRound(s,job.round_id,queue);
  }
  (job.events||=[]).push(d.kind);await service.save(s);return json({ok:true});
 }
 if(b.executor_id!==lease.executor_id||b.lease_token!==lease.lease_token)return path==='/api/begin'?json({permit:false}):json({ok:false},409);
 if(path==='/api/begin'){let j=s.jobs.find(x=>x.id===b.job_id&&x.lease===run);if(!j||j.status!=='claimed'||!ready(s)||j.config_version!==s.config_version||!jobValid(s,j))return json({permit:false});j.status='begun';await service.save(s);return json({permit:true})}
 if(path==='/api/finish'){finishLease(s);if(ready(s)&&s.jobs.some(j=>j.status==='queued'))s.dispatch={at:Date.now(),attempt:0};await service.save(s);return json({ok:true})}
 return json({ok:false},404)
}
